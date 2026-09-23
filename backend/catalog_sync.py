"""Persistent, bounded background synchronization of the EKT product catalog."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncSession, async_sessionmaker

from .errors import APIError
from .models import CatalogSyncRun, ProductSnapshot, utcnow
from .runtime_settings import read_settings


# Session-level advisory lock. Only one importer can run across API processes.
LOCK_KEY = 0x4841434B414C454D
ERROR_SAMPLE_LIMIT = 10
MAX_PAGES = 10000  # Safety bound well above the observed catalog size.


class SyncStopped(Exception):
    def __init__(self, reason: str):
        self.reason = reason


def _error_code(exc: BaseException) -> str:
    name = type(exc).__name__
    if name == "CatalogAccessDenied":
        return "CATALOG_ACCESS_DENIED"
    if name == "CatalogUnavailable":
        return "CATALOG_UNAVAILABLE"
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
        return "TIMEOUT"
    if isinstance(exc, (ValueError, TypeError, KeyError)):
        return "INVALID_CATALOG_RESPONSE"
    return "SYNC_ERROR"


def _serialize(run: CatalogSyncRun | None) -> dict | None:
    if run is None:
        return None
    return {
        "id": str(run.id),
        "trigger": run.trigger,
        "mode": run.mode,
        "status": run.status,
        "started_at": run.started_at.isoformat(),
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "discovered_count": run.discovered_count,
        "processed_count": run.processed_count,
        "updated_count": run.updated_count,
        "failed_count": run.failed_count,
        "pages_count": run.pages_count,
        "stop_reason": run.stop_reason,
        "errors": run.errors or [],
        "cancellation_requested": run.cancellation_requested,
    }


class CatalogSynchronizer:
    def __init__(
        self,
        engine: AsyncEngine,
        session_factory: async_sessionmaker[AsyncSession],
        catalog: Any,
        settings: Any,
    ):
        self.engine = engine
        self.session_factory = session_factory
        self.catalog = catalog
        self._base_settings = settings
        self.settings = settings
        self._job_settings = None
        self._settings_wake = asyncio.Event()
        self._launch_lock = asyncio.Lock()
        self._scheduler_task: asyncio.Task | None = None
        self._job_task: asyncio.Task | None = None
        self._lock_connection: AsyncConnection | None = None
        self._cancel_event = asyncio.Event()
        self._active_run_id: uuid.UUID | None = None
        self._shutting_down = False

    @property
    def configured(self) -> bool:
        return bool(self.settings.ekt_api_username and self.settings.ekt_api_password)

    @property
    def job_settings(self):
        # A running import keeps the limits it started with. Edits affect the next run.
        return self._job_settings or self.settings

    async def _refresh_settings(self) -> None:
        async with self.session_factory() as db:
            overrides = await read_settings(db, self._base_settings)
        self.settings = replace(self._base_settings, **overrides)

    async def reload(self) -> dict:
        """Apply durable settings locally and wake this process's scheduler."""
        await self._refresh_settings()
        self._settings_wake.set()
        return {key: getattr(self.settings, key) for key in (
            "catalog_sync_enabled", "catalog_sync_interval_seconds",
            "catalog_sync_concurrency", "catalog_sync_timeout_seconds",
        )}

    async def _wait_for_settings(self, seconds: float = 5.0) -> None:
        try:
            await asyncio.wait_for(self._settings_wake.wait(), timeout=seconds)
        except TimeoutError:
            pass
        self._settings_wake.clear()

    async def start(self) -> None:
        # Recovery must also run when automatic sync is disabled: an old
        # "running" row otherwise blocks the admin page after a process crash.
        try:
            connection = await self.engine.connect()
            try:
                acquired = bool(await connection.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": LOCK_KEY}))
                await connection.commit()
                if acquired:
                    try:
                        async with self.session_factory() as db:
                            await db.execute(
                                update(CatalogSyncRun)
                                .where(CatalogSyncRun.status == "running")
                                .values(status="interrupted", finished_at=utcnow(), stop_reason="previous_process_stopped")
                            )
                            await db.commit()
                    finally:
                        await connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": LOCK_KEY})
                        await connection.commit()
            finally:
                await connection.close()
        except Exception:
            # Readiness exposes database failures; startup remains available.
            pass
        try:
            await self._refresh_settings()
        except Exception:
            # Keep startup available while the database is recovering.
            pass
        if self._scheduler_task is None:
            self._scheduler_task = asyncio.create_task(self._scheduler(), name="catalog-sync-scheduler")

    async def stop(self) -> None:
        self._shutting_down = True
        if self._scheduler_task is not None:
            self._scheduler_task.cancel()
            try:
                await self._scheduler_task
            except asyncio.CancelledError:
                pass
            self._scheduler_task = None
        if self._job_task is not None and not self._job_task.done():
            self._cancel_event.set()
            try:
                await asyncio.wait_for(self._job_task, timeout=max(20.0, self.job_settings.catalog_sync_timeout_seconds * 2 + 2))
            except TimeoutError:
                self._job_task.cancel()
                try:
                    await self._job_task
                except asyncio.CancelledError:
                    pass

    async def get_run(self, run_id: uuid.UUID | str) -> dict | None:
        try:
            identifier = uuid.UUID(str(run_id))
        except ValueError:
            return None
        async with self.session_factory() as db:
            return _serialize(await db.get(CatalogSyncRun, identifier))

    async def status(self) -> dict:
        await self._refresh_settings()
        async with self.session_factory() as db:
            current = await db.scalar(
                select(CatalogSyncRun).where(CatalogSyncRun.status == "running")
                .order_by(CatalogSyncRun.started_at.desc()).limit(1)
            )
            last_success = await db.scalar(
                select(CatalogSyncRun).where(CatalogSyncRun.status == "succeeded")
                .order_by(CatalogSyncRun.finished_at.desc()).limit(1)
            )
            runs = (await db.scalars(
                select(CatalogSyncRun).order_by(CatalogSyncRun.started_at.desc()).limit(10)
            )).all()
            last_finished = await db.scalar(
                select(CatalogSyncRun).where(
                    CatalogSyncRun.finished_at.is_not(None), CatalogSyncRun.status != "interrupted",
                )
                .order_by(CatalogSyncRun.finished_at.desc()).limit(1)
            )
            catalog_count = await db.scalar(select(func.count()).select_from(ProductSnapshot))
        enabled = bool(self.settings.catalog_sync_enabled)
        next_run_at = None
        if enabled and self.configured and current is None:
            due = (last_finished.finished_at + timedelta(seconds=self.settings.catalog_sync_interval_seconds)) if last_finished else utcnow()
            next_run_at = max(due, utcnow()).isoformat()
        scheduler_status = (
            "not_configured" if not self.configured else
            "disabled" if not enabled else
            "running" if current else "scheduled"
        )
        return {
            "enabled": enabled,
            "configured": self.configured,
            "scheduler_status": scheduler_status,
            "interval_seconds": self.settings.catalog_sync_interval_seconds,
            "concurrency": self.settings.catalog_sync_concurrency,
            "timeout_seconds": self.settings.catalog_sync_timeout_seconds,
            "current": _serialize(current),
            "last_success": _serialize(last_success),
            "run_history": [_serialize(row) for row in runs],
            "catalog_count": catalog_count or 0,
            "next_run_at": next_run_at,
        }

    async def trigger(self, mode: str = "full", trigger: str = "manual") -> dict:
        if mode not in {"full", "existing"}:
            raise APIError(422, "INVALID_SYNC_MODE", "Допустимы full и existing")
        if trigger not in {"manual", "automatic"}:
            raise APIError(422, "INVALID_SYNC_TRIGGER", "Недопустимый источник запуска")
        await self._refresh_settings()
        if not self.configured:
            raise APIError(503, "CATALOG_NOT_CONFIGURED", "Доступ к каталогу ЕКТ не настроен")
        if self._shutting_down:
            raise APIError(503, "SYNC_STOPPING", "Сервис останавливается")
        async with self._launch_lock:
            if self._job_task is not None and not self._job_task.done():
                raise APIError(409, "SYNC_IN_PROGRESS", "Синхронизация уже выполняется")
            connection = await self.engine.connect()
            acquired = False
            try:
                acquired = bool(await connection.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": LOCK_KEY}))
                await connection.commit()
                if not acquired:
                    raise APIError(409, "SYNC_IN_PROGRESS", "Синхронизация уже выполняется")
                async with self.session_factory() as db:
                    await db.execute(
                        update(CatalogSyncRun)
                        .where(CatalogSyncRun.status == "running")
                        .values(status="interrupted", finished_at=utcnow(), stop_reason="previous_process_stopped")
                    )
                    run = CatalogSyncRun(mode=mode, trigger=trigger, status="running", errors=[])
                    db.add(run)
                    await db.commit()
                    result = _serialize(run)
                self._cancel_event = asyncio.Event()
                self._active_run_id = run.id
                self._lock_connection = connection
                self._job_settings = self.settings
                self._job_task = asyncio.create_task(self._run(run.id, mode), name=f"catalog-sync-{run.id}")
                return result
            except BaseException:
                if acquired:
                    try:
                        await connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": LOCK_KEY})
                        await connection.commit()
                    finally:
                        await connection.close()
                else:
                    await connection.close()
                raise

    async def cancel(self, run_id: uuid.UUID | str) -> dict:
        try:
            identifier = uuid.UUID(str(run_id))
        except ValueError:
            raise APIError(404, "SYNC_RUN_NOT_FOUND", "Запуск не найден") from None
        async with self.session_factory() as db:
            run = await db.get(CatalogSyncRun, identifier)
            if run is None:
                raise APIError(404, "SYNC_RUN_NOT_FOUND", "Запуск не найден")
            if run.status == "running":
                run.cancellation_requested = True
                await db.commit()
                if self._active_run_id == identifier:
                    self._cancel_event.set()
            return _serialize(run)

    async def _scheduler(self) -> None:
        while True:
            try:
                await self._refresh_settings()
                if not self.settings.catalog_sync_enabled or not self.configured:
                    await self._wait_for_settings()
                    continue
                async with self.session_factory() as db:
                    last_finished = await db.scalar(
                        select(CatalogSyncRun.finished_at)
                        .where(CatalogSyncRun.finished_at.is_not(None), CatalogSyncRun.status != "interrupted")
                        .order_by(CatalogSyncRun.finished_at.desc()).limit(1)
                    )
                due = last_finished + timedelta(seconds=self.settings.catalog_sync_interval_seconds) if last_finished else utcnow()
                delay = max(0.0, (due - utcnow()).total_seconds())
                if delay:
                    await self._wait_for_settings(min(delay, 5.0))
                    continue
                try:
                    await self.trigger(mode="full", trigger="automatic")
                except APIError as exc:
                    if exc.code != "SYNC_IN_PROGRESS":
                        await self._wait_for_settings()
                        continue
                await self._wait_for_settings()
            except asyncio.CancelledError:
                raise
            except Exception:
                # Database may start after the API process; retain the scheduler.
                await self._wait_for_settings()

    async def _check_cancel(self, run_id: uuid.UUID) -> None:
        if self._shutting_down:
            raise SyncStopped("shutdown")
        if self._cancel_event.is_set():
            raise SyncStopped("cancelled")
        async with self.session_factory() as db:
            requested = await db.scalar(select(CatalogSyncRun.cancellation_requested).where(CatalogSyncRun.id == run_id))
        if requested:
            raise SyncStopped("cancelled")

    async def _request(self, call) -> Any:
        for attempt in range(2):
            if self._cancel_event.is_set() or self._shutting_down:
                raise SyncStopped("shutdown" if self._shutting_down else "cancelled")
            try:
                return await call()
            except Exception as exc:
                if type(exc).__name__ == "CatalogAccessDenied" or attempt == 1:
                    raise
                if type(exc).__name__ not in {"CatalogUnavailable", "TimeoutError"}:
                    raise
                await asyncio.sleep(0.25)
        raise RuntimeError("retry loop ended unexpectedly")

    async def _detail(self, product_id: int) -> tuple[bool, str | None]:
        observed = datetime.now(timezone.utc)
        raw = await self._request(lambda: self.catalog.ekt.detail(
            product_id, timeout_seconds=self.job_settings.catalog_sync_timeout_seconds,
        ))
        if not isinstance(raw, dict) or int(raw.get("id", -1)) != product_id:
            raise ValueError("product ID mismatch")
        stored = await self.catalog.save_raw(raw, checked_at=observed)
        observed_text = observed.isoformat().replace("+00:00", "Z")
        return stored.get("checked_at") == observed_text, None

    async def _progress(
        self, run_id: uuid.UUID, *, discovered: int = 0, processed: int = 0,
        updated: int = 0, failed: int = 0, pages: int = 0, errors: list[dict] | None = None,
    ) -> None:
        async with self.session_factory() as db:
            run = await db.get(CatalogSyncRun, run_id)
            run.discovered_count += discovered
            run.processed_count += processed
            run.updated_count += updated
            run.failed_count += failed
            run.pages_count += pages
            if errors:
                run.errors = ((run.errors or []) + errors)[:ERROR_SAMPLE_LIMIT]
            await db.commit()

    async def _process_ids(self, run_id: uuid.UUID, ids: list[int]) -> None:
        width = self.job_settings.catalog_sync_concurrency
        for offset in range(0, len(ids), width):
            await self._check_cancel(run_id)
            batch = ids[offset:offset + width]
            results = await asyncio.gather(*(self._detail(pid) for pid in batch), return_exceptions=True)
            updated = failed = 0
            errors = []
            access_denied = False
            for pid, result in zip(batch, results):
                if isinstance(result, BaseException):
                    failed += 1
                    code = _error_code(result)
                    errors.append({"product_id": pid, "code": code})
                    access_denied = access_denied or code == "CATALOG_ACCESS_DENIED"
                elif result[0]:
                    updated += 1
            await self._progress(run_id, processed=len(batch), updated=updated, failed=failed, errors=errors)
            if access_denied:
                raise SyncStopped("catalog_access_denied")

    async def _run_full(self, run_id: uuid.UUID) -> str:
        page_number = 1
        seen_pages: set[tuple[int, ...]] = set()
        seen_ids: set[int] = set()
        while True:
            await self._check_cancel(run_id)
            page = await self._request(lambda: self.catalog.ekt.page(
                page_number, per_page=100, timeout_seconds=self.job_settings.catalog_sync_timeout_seconds,
            ))
            if not isinstance(page, list):
                raise ValueError("catalog page is not a list")
            ids = [int(item["id"]) for item in page if isinstance(item, dict) and item.get("id")]
            signature = tuple(ids)
            if not ids:
                await self._progress(run_id, pages=1)
                return "empty_page"
            if signature in seen_pages:
                await self._progress(run_id, pages=1)
                return "repeated_page"
            seen_pages.add(signature)
            new_ids = list(dict.fromkeys(pid for pid in ids if pid > 0 and pid not in seen_ids))
            if not new_ids:
                await self._progress(run_id, pages=1)
                return "no_new_ids"
            seen_ids.update(new_ids)
            await self._progress(run_id, discovered=len(new_ids), pages=1)
            await self._process_ids(run_id, new_ids)
            await self._check_cancel(run_id)
            if len(page) < 100:
                return "short_page"
            page_number += 1
            if page_number > MAX_PAGES:
                raise SyncStopped("page_limit")

    async def _run_existing(self, run_id: uuid.UUID) -> str:
        async with self.session_factory() as db:
            ids = list((await db.scalars(select(ProductSnapshot.id).order_by(ProductSnapshot.id))).all())
        await self._progress(run_id, discovered=len(ids))
        await self._process_ids(run_id, ids)
        await self._check_cancel(run_id)
        return "existing_complete"

    async def _run(self, run_id: uuid.UUID, mode: str) -> None:
        outcome = "succeeded"
        reason = None
        try:
            reason = await (self._run_full(run_id) if mode == "full" else self._run_existing(run_id))
        except SyncStopped as exc:
            reason = exc.reason
            outcome = "interrupted" if exc.reason == "shutdown" else "cancelled" if exc.reason == "cancelled" else "failed"
        except asyncio.CancelledError:
            reason = "shutdown"
            outcome = "interrupted"
        except Exception as exc:
            reason = _error_code(exc).lower()
            outcome = "failed"
            await self._progress(run_id, failed=1, errors=[{"code": _error_code(exc)}])
        finally:
            try:
                async with self.session_factory() as db:
                    run = await db.get(CatalogSyncRun, run_id)
                    if run is not None:
                        if outcome == "succeeded" and run.failed_count:
                            outcome = "partial"
                        elif outcome == "failed" and run.updated_count:
                            outcome = "partial"
                        run.status = outcome
                        run.finished_at = utcnow()
                        run.stop_reason = reason
                        await db.commit()
            finally:
                connection = self._lock_connection
                self._lock_connection = None
                self._active_run_id = None
                self._job_settings = None
                if connection is not None:
                    try:
                        await connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": LOCK_KEY})
                        await connection.commit()
                    finally:
                        await connection.close()
