"""Admin model routes with a fake control; never contacts OpenAI."""

import os
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi import FastAPI
import httpx

from backend.admin import router as admin_router
from backend.admin_ai import router as ai_router
from backend.config import get_settings
from backend.errors import APIError, api_error_handler


class FakeControl:
    def __init__(self):
        self.active = "gpt-6-sol"
        self.revision = 0
        self.check = None
        self.probes = 0

    async def list_models(self):
        return {"models": ["gpt-6-sol", "gpt-6-luna"], "checked_at": "2026-09-23T10:00:00Z", "cached": False}

    async def get_status(self):
        return {
            "default_model": "gpt-6-sol", "override_model": self.active if self.revision else None,
            "active_model": self.active, "revision": self.revision, "configured": True,
            "health": "ok", "last_incident": None, "last_checked_at": None,
        }

    async def check_model(self, model, admin_id):
        if model not in {"gpt-6-sol", "gpt-6-luna"}:
            raise APIError(404, "MODEL_NOT_AVAILABLE", "Модель недоступна")
        self.probes += 1
        self.check = {"check_id": str(uuid4()), "model": model, "admin_id": str(admin_id)}
        return {"check_id": self.check["check_id"], "model": model, "status": "passed",
                "checked_at": "2026-09-23T10:00:00Z", "expires_at": "2026-09-23T10:05:00Z"}

    async def activate(self, model, check_id, admin_id):
        if self.check != {"check_id": str(check_id), "model": model, "admin_id": str(admin_id)}:
            raise APIError(409, "MODEL_CHECK_STALE", "Проверка недействительна")
        self.check = None
        self.active = model
        self.revision += 1
        return await self.get_status()


class AdminAIAPITest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.env_patch = patch.dict(os.environ, {
            "ADMIN_PASSWORD": "admin-ai-test-secret", "ADMIN_ORIGIN": "http://localhost:8000",
        })
        self.env_patch.start()
        get_settings.cache_clear()
        app = FastAPI()
        app.add_exception_handler(APIError, api_error_handler)
        app.include_router(admin_router)
        app.include_router(ai_router)
        self.control = FakeControl()
        app.state.ai_models = self.control
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test",
            headers={"Origin": "http://localhost:8000"},
        )

    async def asyncTearDown(self):
        await self.client.aclose()
        self.env_patch.stop()
        get_settings.cache_clear()

    async def test_admin_only_probe_then_one_use_activation(self):
        self.assertEqual((await self.client.get("/api/admin/ai/models")).status_code, 401)
        self.assertEqual((await self.client.get("/api/admin/ai/status")).status_code, 401)
        self.assertEqual((await self.client.post("/api/admin/ai/check", json={"model": "gpt-6-luna"})).status_code, 401)
        page = await self.client.get("/admin")
        self.assertEqual(page.status_code, 200)
        self.assertIn("AI-модель", page.text)
        self.assertNotIn("admin-ai-test-secret", page.text)

        login = await self.client.post("/api/admin/session", json={"password": "admin-ai-test-secret"})
        self.assertEqual(login.status_code, 200, login.text)
        self.assertEqual(login.json()["role"], "admin")
        csrf = login.json()["csrf_token"]
        models = await self.client.get("/api/admin/ai/models")
        self.assertEqual(models.status_code, 200, models.text)
        self.assertEqual(models.json()["models"], ["gpt-6-sol", "gpt-6-luna"])
        self.assertEqual((await self.client.get("/api/admin/ai/status")).json()["active_model"], "gpt-6-sol")
        self.assertEqual(self.control.probes, 0)

        denied = await self.client.post("/api/admin/ai/check", json={"model": "gpt-6-luna"})
        self.assertEqual(denied.status_code, 403)
        wrong_origin = await self.client.post(
            "/api/admin/ai/check", json={"model": "gpt-6-luna"},
            headers={"X-CSRF-Token": csrf, "Origin": "http://example.com"},
        )
        self.assertEqual(wrong_origin.status_code, 403)
        self.assertEqual(self.control.probes, 0)
        headers = {"X-CSRF-Token": csrf}
        unavailable = await self.client.post("/api/admin/ai/check", json={"model": "unknown"}, headers=headers)
        self.assertEqual(unavailable.json()["error"]["code"], "MODEL_NOT_AVAILABLE")
        checked = await self.client.post("/api/admin/ai/check", json={"model": "gpt-6-luna"}, headers=headers)
        self.assertEqual(checked.status_code, 200, checked.text)
        self.assertEqual(checked.json()["status"], "passed")
        self.assertEqual(self.control.probes, 1)
        self.assertEqual((await self.client.get("/api/admin/ai/status")).json()["active_model"], "gpt-6-sol")
        check_id = checked.json()["check_id"]
        missing_csrf = await self.client.post(
            "/api/admin/ai/activate", json={"model": "gpt-6-luna", "check_id": check_id},
        )
        self.assertEqual(missing_csrf.status_code, 403)
        stale = await self.client.post(
            "/api/admin/ai/activate", json={"model": "gpt-6-sol", "check_id": check_id}, headers=headers,
        )
        self.assertEqual(stale.json()["error"]["code"], "MODEL_CHECK_STALE")
        activated = await self.client.post(
            "/api/admin/ai/activate", json={"model": "gpt-6-luna", "check_id": check_id}, headers=headers,
        )
        self.assertEqual(activated.status_code, 200, activated.text)
        self.assertEqual(activated.json()["active_model"], "gpt-6-luna")
        self.assertEqual(activated.json()["revision"], 1)
        reused = await self.client.post(
            "/api/admin/ai/activate", json={"model": "gpt-6-luna", "check_id": check_id}, headers=headers,
        )
        self.assertEqual(reused.json()["error"]["code"], "MODEL_CHECK_STALE")
        self.assertEqual((await self.client.get("/api/admin/ai/status")).json()["active_model"], "gpt-6-luna")
        self.assertEqual((await self.client.delete("/api/admin/session", headers=headers)).status_code, 200)
        self.assertEqual((await self.client.get("/api/admin/ai/status")).status_code, 401)
