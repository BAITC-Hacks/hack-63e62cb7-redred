"""Focused PostgreSQL checks for scheduled and manual catalog synchronization.

Requires a migrated disposable PostgreSQL database in DATABASE_URL. All EKT
responses here are controlled in memory; the test never crawls the live API.
"""

import asyncio
import unittest
import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, text

from backend.catalog import CatalogService
from backend.catalog_sync import CatalogSynchronizer, LOCK_KEY
from backend.config import get_settings
from backend.db import get_engine, get_session_factory
from backend.ekt_client import CatalogUnavailable
from backend.errors import APIError
from backend.models import CatalogSyncRun, ProductSnapshot


IDS = (9910001, 9910002, 9910003, 9910099)


def card(product_id: int, name: str, price: int = 100) -> dict:
    return {"id": product_id, "article": f"SYNC_{product_id}", "name": name,
            "price": price, "quantity": 3}


class FakeEkt:
    def __init__(self, pages, details, held_id=None):
        self.pages = pages
        self.details = details
        self.held_id = held_id
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.page_calls = 0

    async def page(self, number, per_page=100, *, timeout_seconds=None):
        self.page_calls += 1
        return self.pages[min(number - 1, len(self.pages) - 1)]

    async def detail(self, product_id, *, timeout_seconds=None):
        if product_id == self.held_id:
            self.started.set()
            await self.release.wait()
        result = self.details[product_id]
        if isinstance(result, Exception):
            raise result
        return dict(result)


class CatalogSyncTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.managers = []
        self.run_ids = []
        self.settings = replace(
            get_settings(), ekt_api_username="test", ekt_api_password="test",
            catalog_sync_enabled=False, catalog_sync_interval_seconds=-1,
            catalog_sync_timeout_seconds=1.0, catalog_sync_concurrency=2,
        )

    async def asyncTearDown(self):
        for manager in self.managers:
            await manager.stop()
        async with get_session_factory()() as db:
            if self.run_ids:
                await db.execute(delete(CatalogSyncRun).where(
                    CatalogSyncRun.id.in_([uuid.UUID(run_id) for run_id in self.run_ids])
                ))
            await db.execute(delete(ProductSnapshot).where(ProductSnapshot.id.in_(IDS)))
            await db.commit()
        await get_engine().dispose()

    def manager(self, fake, *, scheduled=False):
        catalog = CatalogService(get_session_factory(), None, self.settings)
        catalog.ekt = fake
        settings = replace(self.settings, catalog_sync_enabled=scheduled)
        manager = CatalogSynchronizer(get_engine(), get_session_factory(), catalog, settings)
        self.managers.append(manager)
        return manager

    async def finished(self, manager, run_id):
        for _ in range(100):
            run = await manager.get_run(run_id)
            if run and run["status"] != "running":
                if manager._job_task is not None:
                    await manager._job_task
                return run
            await asyncio.sleep(0.02)
        self.fail("catalog sync did not finish promptly")

    async def test_scheduler_starts_and_cross_manager_lock_then_cancel_releases(self):
        first_fake = FakeEkt([[{"id": IDS[0]}]], {IDS[0]: card(IDS[0], "held")}, held_id=IDS[0])
        first = self.manager(first_fake, scheduled=True)
        second = self.manager(FakeEkt([[{"id": IDS[1]}]], {IDS[1]: card(IDS[1], "next")}))
        await first.start()
        await asyncio.wait_for(first_fake.started.wait(), 3)
        active = (await first.status())["current"]
        self.assertEqual(active["trigger"], "automatic")
        self.run_ids.append(active["id"])
        with self.assertRaises(APIError) as caught:
            await second.trigger(mode="full")
        self.assertEqual(caught.exception.code, "SYNC_IN_PROGRESS")
        await first.cancel(active["id"])
        first_fake.release.set()
        stopped = await self.finished(first, active["id"])
        self.assertEqual(stopped["status"], "cancelled")
        restarted = await second.trigger(mode="full")
        self.run_ids.append(restarted["id"])
        done = await self.finished(second, restarted["id"])
        self.assertEqual(done["status"], "succeeded")
        self.assertEqual(done["stop_reason"], "short_page")

    async def test_partial_failure_preserves_old_snapshot(self):
        older = datetime.now(timezone.utc) - timedelta(minutes=5)
        catalog = CatalogService(get_session_factory(), None, self.settings)
        await catalog.save_raw(card(IDS[1], "old", 10), checked_at=older)
        fake = FakeEkt(
            [[{"id": IDS[1]}, {"id": IDS[2]}]],
            {IDS[1]: CatalogUnavailable("temporary"), IDS[2]: card(IDS[2], "new", 20)},
        )
        manager = self.manager(fake)
        run = await manager.trigger(mode="full")
        self.run_ids.append(run["id"])
        result = await self.finished(manager, run["id"])
        self.assertEqual(result["status"], "partial")
        self.assertEqual((result["processed_count"], result["updated_count"], result["failed_count"]), (2, 1, 1))
        self.assertEqual((await catalog.get_product(IDS[1]))["name"], "old")
        self.assertEqual((await catalog.get_product(IDS[2]))["name"], "new")

    async def test_concurrent_insert_keeps_newest_observation(self):
        catalog = CatalogService(get_session_factory(), None, self.settings)
        now = datetime.now(timezone.utc)
        old, new = await asyncio.gather(
            catalog.save_raw(card(IDS[3], "older", 10), checked_at=now - timedelta(seconds=5)),
            catalog.save_raw(card(IDS[3], "newer", 20), checked_at=now),
        )
        self.assertEqual((await catalog.get_product(IDS[3]))["name"], "newer")
        self.assertEqual((await catalog.get_product(IDS[3]))["price"], "20.00")
        self.assertEqual(new["name"], "newer")
        self.assertIn(old["name"], {"older", "newer"})

    async def test_startup_recovers_stale_run_even_when_scheduler_disabled(self):
        manager = self.manager(FakeEkt([[]], {}))
        async with get_session_factory()() as db:
            stale = CatalogSyncRun(mode="full", trigger="manual", status="running", errors=[])
            db.add(stale)
            await db.commit()
            run_id = str(stale.id)
        self.run_ids.append(run_id)

        # A lock held by another process means its running row is still active.
        connection = await get_engine().connect()
        try:
            await connection.execute(text("SELECT pg_advisory_lock(:key)"), {"key": LOCK_KEY})
            await connection.commit()
            await manager.start()
            self.assertEqual((await manager.get_run(run_id))["status"], "running")
        finally:
            await connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": LOCK_KEY})
            await connection.commit()
            await connection.close()

        await manager.start()
        recovered = await manager.get_run(run_id)
        self.assertEqual(recovered["status"], "interrupted")
        self.assertEqual(recovered["stop_reason"], "previous_process_stopped")
        self.assertIsNone((await manager.status())["current"])

    async def test_repeated_and_empty_page_stop_without_crawl(self):
        ids = [{"id": 9920000 + i} for i in range(100)]
        fake = FakeEkt([ids, ids], {})
        manager = self.manager(fake)

        async def skip_details(_run_id, _ids):
            return None

        manager._process_ids = skip_details
        first = await manager.trigger(mode="full")
        self.run_ids.append(first["id"])
        repeated = await self.finished(manager, first["id"])
        self.assertEqual(repeated["stop_reason"], "repeated_page")
        self.assertEqual((repeated["pages_count"], fake.page_calls), (2, 2))
        fake.pages = [[]]
        fake.page_calls = 0
        second = await manager.trigger(mode="full")
        self.run_ids.append(second["id"])
        empty = await self.finished(manager, second["id"])
        self.assertEqual(empty["stop_reason"], "empty_page")
        self.assertEqual(fake.page_calls, 1)


if __name__ == "__main__":
    unittest.main()
