"""Focused PostgreSQL checks for durable, live catalog sync settings."""

import asyncio
import unittest
import uuid
from dataclasses import replace

from sqlalchemy import delete, select

from backend.catalog import CatalogService
from backend.catalog_sync import CatalogSynchronizer
from backend.config import get_settings
from backend.db import get_engine, get_session_factory
from backend.errors import APIError
from backend.models import CatalogSyncRun, RuntimeSetting
from backend.runtime_settings import KEYS, read_settings, update_settings


class WaitingEkt:
    def __init__(self):
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.timeouts = []

    async def page(self, number, per_page=100, *, timeout_seconds=None):
        self.timeouts.append(timeout_seconds)
        self.started.set()
        await self.release.wait()
        return []


class RuntimeSettingsTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.managers = []
        self.run_ids = []
        async with get_session_factory()() as db:
            self.original = (await db.scalars(select(RuntimeSetting).where(RuntimeSetting.key.in_(KEYS)))).all()
            self.original = [(row.key, row.value, row.updated_at) for row in self.original]
            await db.execute(delete(RuntimeSetting).where(RuntimeSetting.key.in_(KEYS)))
            await db.commit()

    async def asyncTearDown(self):
        for manager in self.managers:
            await manager.stop()
        async with get_session_factory()() as db:
            if self.run_ids:
                await db.execute(delete(CatalogSyncRun).where(
                    CatalogSyncRun.id.in_([uuid.UUID(str(run_id)) for run_id in self.run_ids])
                ))
            await db.execute(delete(RuntimeSetting).where(RuntimeSetting.key.in_(KEYS)))
            for key, value, updated_at in self.original:
                db.add(RuntimeSetting(key=key, value=value, updated_at=updated_at))
            await db.commit()
        await get_engine().dispose()

    async def test_allowlist_validation_and_durable_values(self):
        async with get_session_factory()() as db:
            values = await update_settings(db, {
                "catalog_sync_enabled": False,
                "catalog_sync_interval_seconds": 60,
                "catalog_sync_concurrency": 1,
                "catalog_sync_timeout_seconds": 3.5,
            })
            self.assertEqual(values["catalog_sync_interval_seconds"], 60)
            self.assertEqual(values["catalog_sync_timeout_seconds"], 3.5)
            for patch in (
                {"admin_password": "secret"},
                {"catalog_sync_enabled": 1},
                {"catalog_sync_interval_seconds": 59},
                {"catalog_sync_concurrency": 3},
                {"catalog_sync_timeout_seconds": float("nan")},
            ):
                with self.assertRaises(APIError) as caught:
                    await update_settings(db, patch)
                self.assertEqual(caught.exception.code, "INVALID_RUNTIME_SETTINGS")
        async with get_session_factory()() as db:
            self.assertEqual(await read_settings(db), values)

    async def test_enable_wakes_scheduler_and_running_job_keeps_limits(self):
        settings = replace(
            get_settings(), ekt_api_username="fake", ekt_api_password="fake",
            catalog_sync_enabled=False, catalog_sync_interval_seconds=-1,
            catalog_sync_timeout_seconds=2.0,
        )
        fake = WaitingEkt()
        catalog = CatalogService(get_session_factory(), None, settings)
        catalog.ekt = fake
        manager = CatalogSynchronizer(get_engine(), get_session_factory(), catalog, settings)
        self.managers.append(manager)
        await manager.start()
        self.assertFalse(fake.started.is_set())

        async with get_session_factory()() as db:
            await update_settings(db, {"catalog_sync_enabled": True})
        await manager.reload()
        await asyncio.wait_for(fake.started.wait(), 3)
        active_id = manager._active_run_id
        self.run_ids.append(active_id)
        self.assertEqual(manager.job_settings.catalog_sync_timeout_seconds, 2.0)

        async with get_session_factory()() as db:
            await update_settings(db, {"catalog_sync_enabled": False, "catalog_sync_timeout_seconds": 4.0})
        await manager.reload()
        self.assertFalse(manager.settings.catalog_sync_enabled)
        self.assertEqual(manager.job_settings.catalog_sync_timeout_seconds, 2.0)
        fake.release.set()
        await manager._job_task
        self.assertEqual((await manager.get_run(active_id))["status"], "succeeded")

        manual = await manager.trigger(mode="full", trigger="manual")
        self.run_ids.append(manual["id"])
        await manager._job_task
        self.assertEqual((await manager.get_run(manual["id"]))["status"], "succeeded")
        self.assertEqual(fake.timeouts, [2.0, 4.0])


if __name__ == "__main__":
    unittest.main()
