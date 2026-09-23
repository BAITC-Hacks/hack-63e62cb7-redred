"""Durable, checked model selection and guarded fallback for the assistant."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import os
import re
import time
import uuid
from typing import Any

import httpx
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .errors import APIError
from .models import RuntimeSetting, utcnow


CONTROL_KEY = "assistant.model_control"
CHECK_PREFIX = "assistant.model_check."
CHECK_TTL = timedelta(minutes=5)
LIST_TTL_SECONDS = 300
MODEL_LIST_LIMIT = 500
_TEXT_MODEL = re.compile(r"^(?:gpt-[a-z0-9]|o[0-9])", re.IGNORECASE)
_NON_TEXT = ("audio", "realtime", "transcribe", "tts", "image", "embedding", "moderation", "speech", "video", "whisper", "dall-e")


@dataclass(frozen=True)
class ModelSelection:
    model: str
    default_model: str
    revision: int
    is_override: bool


def _iso(value: datetime | None) -> str | None:
    return value.isoformat().replace("+00:00", "Z") if value else None


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _default_state() -> dict:
    return {
        "override_model": None,
        "revision": 0,
        "health": "unknown",
        "last_incident": None,
        "last_checked_at": None,
        "last_check": None,
        "last_success_at": None,
    }


def _candidate(model_id: str) -> bool:
    lower = model_id.casefold()
    return bool(_TEXT_MODEL.match(model_id)) and not any(part in lower for part in _NON_TEXT)


class AIModelControl:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        http_client: httpx.AsyncClient,
        settings: Any = None,
    ):
        self.session_factory = session_factory
        self.http_client = http_client
        self.default_model = os.getenv("OPENAI_MODEL", "gpt-6-sol").strip() or "gpt-6-sol"
        self._cache: tuple[float, str, list[str], str, str] | None = None
        self._list_lock = asyncio.Lock()

    @property
    def configured(self) -> bool:
        return bool(self._keys())

    def _keys(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(key for key in (
            os.getenv("OPENAI_API_KEY", "").strip(),
            os.getenv("OPENAI_FALLBACK_API_KEY", "").strip(),
        ) if key))

    def _fingerprint(self) -> str:
        primary = os.getenv("OPENAI_API_KEY", "").strip()
        backup = os.getenv("OPENAI_FALLBACK_API_KEY", "").strip()
        if not (primary or backup):
            raise APIError(503, "OPENAI_NOT_CONFIGURED", "Ключ OpenAI не настроен")
        return hashlib.sha256((primary + "\0" + backup).encode("utf-8")).hexdigest()

    async def _read_state(self, db: AsyncSession) -> dict:
        row = await db.get(RuntimeSetting, CONTROL_KEY)
        return {**_default_state(), **(row.value if row and isinstance(row.value, dict) else {})}

    async def _locked_state(self, db: AsyncSession) -> tuple[RuntimeSetting, dict]:
        await db.execute(pg_insert(RuntimeSetting).values(
            key=CONTROL_KEY, value=_default_state(), updated_at=utcnow(),
        ).on_conflict_do_nothing(index_elements=[RuntimeSetting.key]))
        row = await db.scalar(select(RuntimeSetting).where(RuntimeSetting.key == CONTROL_KEY).with_for_update())
        return row, {**_default_state(), **(row.value if isinstance(row.value, dict) else {})}

    def _active(self, state: dict) -> str:
        return state.get("override_model") or self.default_model

    def _matches(self, state: dict, selection: ModelSelection) -> bool:
        return (
            state.get("revision") == selection.revision
            and self._active(state) == selection.model
            and self.default_model == selection.default_model
            and bool(state.get("override_model")) == selection.is_override
        )

    async def _list_with_key(self, *, force: bool = False) -> tuple[dict, str]:
        keys = self._keys()
        fingerprint = self._fingerprint()
        now = time.monotonic()
        cached = self._cache
        if not force and cached and cached[0] > now and cached[1] == fingerprint:
            return {"models": list(cached[2]), "checked_at": cached[3], "cached": True}, cached[4]
        async with self._list_lock:
            cached = self._cache
            now = time.monotonic()
            if not force and cached and cached[0] > now and cached[1] == fingerprint:
                return {"models": list(cached[2]), "checked_at": cached[3], "cached": True}, cached[4]
            last_error = None
            for key in keys:
                try:
                    response = await self.http_client.get(
                        "https://api.openai.com/v1/models",
                        headers={"Authorization": f"Bearer {key}"}, timeout=26.0,
                    )
                    if response.status_code in (401, 403):
                        raise APIError(502, "OPENAI_AUTH_FAILED", "OpenAI отклонил серверный ключ")
                    if response.status_code == 429:
                        raise APIError(503, "OPENAI_RATE_LIMITED", "OpenAI временно ограничил запросы")
                    if response.status_code >= 400:
                        raise APIError(502, "MODEL_LIST_UNAVAILABLE", "Список моделей OpenAI сейчас недоступен")
                    payload = response.json()
                    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
                        raise ValueError("invalid model list")
                    models = sorted({
                        item["id"] for item in payload["data"]
                        if isinstance(item, dict) and isinstance(item.get("id"), str)
                        and len(item["id"]) <= 128 and _candidate(item["id"])
                    })[:MODEL_LIST_LIMIT]
                except httpx.HTTPError:
                    last_error = APIError(502, "MODEL_LIST_UNAVAILABLE", "Не удалось получить список моделей OpenAI")
                except (ValueError, TypeError, KeyError):
                    last_error = APIError(502, "MODEL_LIST_INVALID", "OpenAI вернул некорректный список моделей")
                except APIError as exc:
                    last_error = exc
                else:
                    checked_at = _iso(utcnow())
                    self._cache = (time.monotonic() + LIST_TTL_SECONDS, fingerprint, models, checked_at, key)
                    return {"models": list(models), "checked_at": checked_at, "cached": False}, key
            raise last_error

    async def list_models(self, *, force: bool = False) -> dict:
        result, _ = await self._list_with_key(force=force)
        return result

    async def get_status(self) -> dict:
        async with self.session_factory() as db:
            state = await self._read_state(db)
        return {
            "default_model": self.default_model,
            "override_model": state["override_model"],
            "active_model": self._active(state),
            "revision": state["revision"],
            "configured": self.configured,
            "health": "not_configured" if not self.configured else state["health"],
            "last_incident": state["last_incident"],
            "last_checked_at": state["last_checked_at"],
            "last_check": state["last_check"],
            "last_success_at": state["last_success_at"],
        }

    async def selection(self) -> ModelSelection:
        async with self.session_factory() as db:
            state = await self._read_state(db)
        return ModelSelection(
            model=self._active(state), default_model=self.default_model,
            revision=int(state["revision"]), is_override=bool(state["override_model"]),
        )

    async def check_model(self, model: str, admin_id: str | uuid.UUID) -> dict:
        if not isinstance(model, str) or len(model) > 128:
            raise APIError(422, "INVALID_MODEL", "Некорректный ID модели")
        model = model.strip()
        if not model:
            raise APIError(422, "INVALID_MODEL", "Укажите модель")
        fingerprint = self._fingerprint()
        before = await self.selection()
        available, selected_key = await self._list_with_key(force=True)
        if model not in available["models"]:
            raise APIError(422, "MODEL_NOT_AVAILABLE", "Эта модель отсутствует в доступном списке OpenAI")
        try:
            from .assistant.service import probe_model

            await probe_model(self.http_client, model, api_key=selected_key)
        except APIError:
            raise
        except Exception as exc:
            code = str(getattr(exc, "code", "MODEL_CHECK_FAILED"))[:64]
            message = str(getattr(exc, "admin_message", "Модель не прошла проверку функций ассистента"))[:200]
            async with self.session_factory() as db:
                row, state = await self._locked_state(db)
                state["last_checked_at"] = _iso(utcnow())
                state["last_check"] = {"model": model, "status": "failed", "code": code}
                row.value = state
                row.updated_at = utcnow()
                await db.commit()
            raise APIError(502, "MODEL_CHECK_FAILED", message, {"reason": code}) from None
        checked = utcnow()
        expires = checked + CHECK_TTL
        check_id = uuid.uuid4()
        async with self.session_factory() as db:
            row, state = await self._locked_state(db)
            if state["revision"] != before.revision or self.default_model != before.default_model or self._fingerprint() != fingerprint:
                raise APIError(409, "MODEL_CHECK_STALE", "Настройка или ключ изменились во время проверки")
            await db.execute(delete(RuntimeSetting).where(
                RuntimeSetting.key.like(CHECK_PREFIX + "%"),
                RuntimeSetting.updated_at < checked - timedelta(minutes=10),
            ))
            db.add(RuntimeSetting(
                key=CHECK_PREFIX + str(check_id),
                value={
                    "model": model, "admin_id": str(admin_id), "key_fingerprint": fingerprint,
                    "revision": before.revision, "default_model": self.default_model,
                    "checked_at": _iso(checked), "expires_at": _iso(expires), "used": False,
                },
                updated_at=checked,
            ))
            state["last_checked_at"] = _iso(checked)
            state["last_check"] = {"model": model, "status": "passed"}
            row.value = state
            row.updated_at = checked
            await db.commit()
        return {"check_id": str(check_id), "model": model, "status": "passed", "checked_at": _iso(checked), "expires_at": _iso(expires)}

    async def activate(self, model: str, check_id: str | uuid.UUID, admin_id: str | uuid.UUID) -> dict:
        try:
            identifier = uuid.UUID(str(check_id))
        except ValueError:
            raise APIError(422, "MODEL_CHECK_INVALID", "Некорректная проверка модели") from None
        async with self.session_factory() as db:
            row, state = await self._locked_state(db)
            check = await db.scalar(
                select(RuntimeSetting).where(RuntimeSetting.key == CHECK_PREFIX + str(identifier)).with_for_update()
            )
            if check is None or not isinstance(check.value, dict):
                raise APIError(404, "MODEL_CHECK_NOT_FOUND", "Проверка модели не найдена")
            proof = check.value
            if proof.get("used"):
                raise APIError(409, "MODEL_CHECK_USED", "Проверка уже использована")
            if _parse_iso(proof["expires_at"]) <= utcnow():
                raise APIError(409, "MODEL_CHECK_EXPIRED", "Срок проверки модели истёк")
            if proof.get("admin_id") != str(admin_id) or proof.get("model") != model:
                raise APIError(403, "MODEL_CHECK_MISMATCH", "Проверка не принадлежит этому запросу")
            if (
                proof.get("key_fingerprint") != self._fingerprint()
                or proof.get("revision") != state["revision"]
                or proof.get("default_model") != self.default_model
            ):
                raise APIError(409, "MODEL_CHECK_STALE", "Ключ или настройка модели изменились; проверьте снова")
            state["override_model"] = None if model == self.default_model else model
            state["revision"] = int(state["revision"]) + 1
            state["health"] = "healthy"
            state["last_success_at"] = proof["checked_at"]
            check.value = {**proof, "used": True}
            row.value = state
            row.updated_at = utcnow()
            await db.commit()
        return await self.get_status()

    async def record_success(self, selection: ModelSelection) -> None:
        async with self.session_factory() as db:
            row, state = await self._locked_state(db)
            if not self._matches(state, selection):
                return
            state["health"] = "healthy"
            state["last_success_at"] = _iso(utcnow())
            row.value = state
            row.updated_at = utcnow()
            await db.commit()

    async def record_failure(self, selection: ModelSelection, failure: Any) -> bool:
        code = str(getattr(failure, "code", "PROVIDER_UNAVAILABLE"))[:64]
        fallback_allowed = bool(getattr(failure, "fallback_allowed", False))
        async with self.session_factory() as db:
            row, state = await self._locked_state(db)
            if not self._matches(state, selection):
                return False
            incident = {"at": _iso(utcnow()), "model": selection.model, "code": code}
            if selection.is_override and fallback_allowed:
                state["override_model"] = None
                state["revision"] = int(state["revision"]) + 1
                state["health"] = "fallback_pending"
                incident["fallback_to"] = self.default_model
                activated = True
            else:
                state["health"] = "unavailable"
                activated = False
            state["last_incident"] = incident
            row.value = state
            row.updated_at = utcnow()
            await db.commit()
            return activated
