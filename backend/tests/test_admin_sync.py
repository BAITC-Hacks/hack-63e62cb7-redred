"""Focused admin API check with a fake synchronizer; no EKT calls or job starts."""

import os
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import uuid4

import httpx

from backend.config import get_settings
from backend.errors import APIError


class FakeSync:
    def __init__(self):
        self.current = None
        self.trigger_count = 0

    async def status(self):
        return {
            "enabled": True, "configured": True, "catalog_count": 21,
            "current": self.current, "last_success": None, "run_history": [],
            "next_run_at": None, "scheduler_status": "running",
        }

    async def trigger(self, mode, trigger):
        if self.current:
            raise APIError(409, "SYNC_IN_PROGRESS", "Обновление уже идёт")
        self.trigger_count += 1
        self.current = {"id": str(uuid4()), "mode": mode, "trigger": trigger, "status": "running"}
        return self.current

    async def cancel(self, run_id):
        if not self.current or self.current["id"] != str(run_id):
            raise APIError(404, "SYNC_RUN_NOT_FOUND", "Запуск не найден")
        self.current["cancellation_requested"] = True
        return self.current


class AdminSyncTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.env_patch = patch.dict(os.environ, {"ADMIN_PASSWORD": "test-secret-for-admin", "ADMIN_ORIGIN": "http://localhost:8000"})
        self.env_patch.start()
        get_settings.cache_clear()
        from backend.main import app

        self.app = app
        self.sync = FakeSync()
        self.app.state.catalog_sync = self.sync
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test",
            headers={"Origin": "http://localhost:8000"},
        )

    async def asyncTearDown(self):
        await self.client.aclose()
        self.env_patch.stop()
        get_settings.cache_clear()

    async def test_login_access_csrf_trigger_cancel_logout(self):
        page = await self.client.get("/admin/catalog")
        self.assertEqual(page.status_code, 200)
        self.assertIn('id="root"', page.text)
        self.assertNotIn("test-secret-for-admin", page.text)
        self.assertEqual((await self.client.get("/api/admin/catalog/sync")).status_code, 401)

        wrong_origin = await self.client.post(
            "/api/admin/session", json={"password": "test-secret-for-admin"},
            headers={"Origin": "http://example.com"},
        )
        self.assertEqual(wrong_origin.status_code, 403)
        bad_password = await self.client.post("/api/admin/session", json={"password": "incorrect"})
        self.assertEqual(bad_password.status_code, 401)
        login = await self.client.post("/api/admin/session", json={"password": "test-secret-for-admin"})
        self.assertEqual(login.status_code, 200, login.text)
        self.assertIn("httponly", login.headers["set-cookie"].lower())
        csrf = login.json()["csrf_token"]
        status = await self.client.get("/api/admin/catalog/sync")
        self.assertEqual(status.status_code, 200)
        self.assertEqual(status.json()["catalog_count"], 21)

        missing_csrf = await self.client.post("/api/admin/catalog/sync", json={"mode": "existing"})
        self.assertEqual(missing_csrf.status_code, 403)
        wrong_origin = await self.client.post(
            "/api/admin/catalog/sync", json={"mode": "existing"},
            headers={"Origin": "http://example.com", "X-CSRF-Token": csrf},
        )
        self.assertEqual(wrong_origin.status_code, 403)
        headers = {"X-CSRF-Token": csrf}
        trigger = await self.client.post("/api/admin/catalog/sync", json={"mode": "full"}, headers=headers)
        self.assertEqual(trigger.status_code, 202, trigger.text)
        self.assertEqual(self.sync.trigger_count, 1)
        duplicate = await self.client.post("/api/admin/catalog/sync", json={"mode": "existing"}, headers=headers)
        self.assertEqual(duplicate.status_code, 409)
        self.assertEqual(self.sync.trigger_count, 1)
        run_id = trigger.json()["id"]
        cancel = await self.client.post(f"/api/admin/catalog/sync/{run_id}/cancel", headers=headers)
        self.assertEqual(cancel.status_code, 202, cancel.text)
        self.assertTrue(cancel.json()["cancellation_requested"])
        logout = await self.client.delete("/api/admin/session", headers=headers)
        self.assertEqual(logout.status_code, 200)
        self.assertEqual((await self.client.get("/api/admin/catalog/sync")).status_code, 401)
