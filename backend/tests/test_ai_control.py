"""Focused PostgreSQL checks for durable, checked AI model selection.

Requires a migrated disposable DATABASE_URL. Provider traffic is mocked.
"""

import os
import sys
import types
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import httpx
from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.ai_control import AIModelControl, CHECK_PREFIX, CONTROL_KEY
from backend.ai_provider import AIProviderFailure
from backend.errors import APIError
from backend.models import RuntimeSetting


class AIModelControlTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.env = patch.dict(os.environ, {
            "OPENAI_API_KEY": "sk-test-ai-control-only",
            "OPENAI_FALLBACK_API_KEY": "",
            "OPENAI_MODEL": "gpt-6-sol",
        })
        self.env.start()
        self.engine = create_async_engine(os.environ["DATABASE_URL"], poolclass=NullPool)
        self.factory = async_sessionmaker(self.engine, expire_on_commit=False)
        self.saved = []
        async with self.factory() as db:
            rows = (await db.scalars(select(RuntimeSetting).where(or_(
                RuntimeSetting.key == CONTROL_KEY,
                RuntimeSetting.key.like(CHECK_PREFIX + "%"),
            )))).all()
            self.saved = [(row.key, row.value, row.updated_at) for row in rows]
            await db.execute(delete(RuntimeSetting).where(or_(
                RuntimeSetting.key == CONTROL_KEY,
                RuntimeSetting.key.like(CHECK_PREFIX + "%"),
            )))
            await db.commit()
        self.list_calls = 0

        def fake_models(request):
            self.list_calls += 1
            self.assertEqual(request.headers["authorization"], "Bearer sk-test-ai-control-only")
            return httpx.Response(200, json={"data": [
                {"id": "gpt-6-sol"}, {"id": "gpt-4.1"}, {"id": "o3"},
                {"id": "gpt-4o-realtime-preview"}, {"id": "text-embedding-3-large"},
            ]})

        self.client = httpx.AsyncClient(transport=httpx.MockTransport(fake_models))
        self.control = AIModelControl(self.factory, self.client)
        self.fake_service = types.ModuleType("backend.assistant.service")
        self.fake_service.probe_model = None
        self.service_module = patch.dict(sys.modules, {"backend.assistant.service": self.fake_service})
        self.service_module.start()

    async def asyncTearDown(self):
        await self.client.aclose()
        async with self.factory() as db:
            await db.execute(delete(RuntimeSetting).where(or_(
                RuntimeSetting.key == CONTROL_KEY,
                RuntimeSetting.key.like(CHECK_PREFIX + "%"),
            )))
            for key, value, updated_at in self.saved:
                db.add(RuntimeSetting(key=key, value=value, updated_at=updated_at))
            await db.commit()
        await self.engine.dispose()
        self.service_module.stop()
        self.env.stop()

    async def test_checked_activation_and_fallback(self):
        admin = uuid.uuid4()
        other_admin = uuid.uuid4()
        probes = []

        async def probe(_client, model, *, api_key=None):
            probes.append(model)
            self.assertEqual(api_key, "sk-test-ai-control-only")

        with patch.object(self.fake_service, "probe_model", new=probe):
            first = await self.control.list_models()
            self.assertEqual(first["models"], ["gpt-4.1", "gpt-6-sol", "o3"])
            self.assertFalse(first["cached"])
            self.assertTrue((await self.control.list_models())["cached"])
            self.assertEqual(self.list_calls, 1)
            with self.assertRaises(APIError) as missing:
                await self.control.check_model("invented-model", admin)
            self.assertEqual(missing.exception.code, "MODEL_NOT_AVAILABLE")
            self.assertEqual(probes, [])

            checked = await self.control.check_model("gpt-4.1", admin)
            self.assertEqual((await self.control.get_status())["active_model"], "gpt-6-sol")
            with self.assertRaises(APIError) as wrong_owner:
                await self.control.activate("gpt-4.1", checked["check_id"], other_admin)
            self.assertEqual(wrong_owner.exception.code, "MODEL_CHECK_MISMATCH")
            activated = await self.control.activate("gpt-4.1", checked["check_id"], admin)
            self.assertEqual(activated["active_model"], "gpt-4.1")
            self.assertEqual(activated["revision"], 1)
            self.assertEqual(activated["health"], "healthy")
            self.assertEqual(activated["last_success_at"], checked["checked_at"])
            with self.assertRaises(APIError) as replay:
                await self.control.activate("gpt-4.1", checked["check_id"], admin)
            self.assertEqual(replay.exception.code, "MODEL_CHECK_USED")

            selected = await self.control.selection()
            self.assertTrue(selected.is_override)
            self.assertTrue(await self.control.record_failure(
                selected, AIProviderFailure("model_not_found", "safe failure", True, 404),
            ))
            fallback = await self.control.get_status()
            self.assertEqual(fallback["active_model"], "gpt-6-sol")
            self.assertEqual(fallback["health"], "fallback_pending")
            self.assertEqual(fallback["revision"], 2)
            self.assertEqual(fallback["last_incident"]["fallback_to"], "gpt-6-sol")
            await self.control.record_success(selected)
            self.assertEqual((await self.control.get_status())["health"], "fallback_pending")
            await self.control.record_success(await self.control.selection())
            healthy = await self.control.get_status()
            self.assertEqual(healthy["health"], "healthy")
            self.assertIsNotNone(healthy["last_incident"])

            checked2 = await self.control.check_model("gpt-4.1", admin)
            await self.control.activate("gpt-4.1", checked2["check_id"], admin)
            selected2 = await self.control.selection()
            self.assertFalse(await self.control.record_failure(
                selected2, AIProviderFailure("insufficient_quota", "safe failure", False, 429),
            ))
            failed = await self.control.get_status()
            self.assertEqual(failed["active_model"], "gpt-4.1")
            self.assertEqual(failed["health"], "unavailable")

            reset = await self.control.check_model("gpt-6-sol", admin)
            final = await self.control.activate("gpt-6-sol", reset["check_id"], admin)
            self.assertIsNone(final["override_model"])
            self.assertEqual(final["active_model"], "gpt-6-sol")
            self.assertFalse(await self.control.record_failure(
                await self.control.selection(),
                AIProviderFailure("server_error", "safe failure", True, 500),
            ))
            self.assertEqual((await self.control.get_status())["health"], "unavailable")
            self.assertEqual(probes, ["gpt-4.1", "gpt-4.1", "gpt-6-sol"])

    async def test_failed_stale_and_expired_checks_do_not_activate(self):
        admin = uuid.uuid4()

        async def rejected(_client, _model, *, api_key=None):
            raise AIProviderFailure("unsupported_parameter", "Required tool format unsupported", False, 400)

        with patch.object(self.fake_service, "probe_model", new=rejected):
            with self.assertRaises(APIError) as failed:
                await self.control.check_model("o3", admin)
            self.assertEqual(failed.exception.code, "MODEL_CHECK_FAILED")
            self.assertEqual((await self.control.get_status())["active_model"], "gpt-6-sol")

        async def passed(_client, _model, *, api_key=None):
            return None

        with patch.object(self.fake_service, "probe_model", new=passed):
            check = await self.control.check_model("gpt-4.1", admin)
            with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-rotated-test-key"}):
                with self.assertRaises(APIError) as rotated:
                    await self.control.activate("gpt-4.1", check["check_id"], admin)
                self.assertEqual(rotated.exception.code, "MODEL_CHECK_STALE")
            async with self.factory() as db:
                proof = await db.get(RuntimeSetting, CHECK_PREFIX + check["check_id"])
                proof.value = {**proof.value, "expires_at": (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()}
                await db.commit()
            with self.assertRaises(APIError) as expired:
                await self.control.activate("gpt-4.1", check["check_id"], admin)
            self.assertEqual(expired.exception.code, "MODEL_CHECK_EXPIRED")
            self.assertEqual((await self.control.get_status())["revision"], 0)

    async def test_backup_only_and_rejected_primary_use_matching_probe_key(self):
        seen = []
        probed = []

        def models(request):
            authorization = request.headers["authorization"]
            seen.append(authorization)
            if authorization == "Bearer sk-rejected-primary":
                return httpx.Response(401, json={"error": {"message": "private provider text"}})
            self.assertEqual(authorization, "Bearer sk-working-backup")
            return httpx.Response(200, json={"data": [{"id": "gpt-4.1"}]})

        async def probe(_client, model, *, api_key=None):
            probed.append((model, api_key))

        await self.client.aclose()
        self.client = httpx.AsyncClient(transport=httpx.MockTransport(models))
        self.control = AIModelControl(self.factory, self.client)
        admin = uuid.uuid4()
        with patch.object(self.fake_service, "probe_model", new=probe):
            with patch.dict(os.environ, {
                "OPENAI_API_KEY": "", "OPENAI_FALLBACK_API_KEY": "sk-working-backup",
            }):
                self.assertTrue((await self.control.get_status())["configured"])
                only = await self.control.check_model("gpt-4.1", admin)
                self.assertEqual(probed[-1], ("gpt-4.1", "sk-working-backup"))
                self.assertEqual(seen, ["Bearer sk-working-backup"])
                self.assertEqual((await self.control.activate("gpt-4.1", only["check_id"], admin))["active_model"], "gpt-4.1")
            with patch.dict(os.environ, {
                "OPENAI_API_KEY": "sk-rejected-primary", "OPENAI_FALLBACK_API_KEY": "sk-working-backup",
            }):
                checked = await self.control.check_model("gpt-4.1", admin)
                self.assertEqual(seen[-2:], ["Bearer sk-rejected-primary", "Bearer sk-working-backup"])
                self.assertEqual(probed[-1], ("gpt-4.1", "sk-working-backup"))
                with patch.dict(os.environ, {"OPENAI_FALLBACK_API_KEY": "sk-rotated-backup"}):
                    with self.assertRaises(APIError) as stale:
                        await self.control.activate("gpt-4.1", checked["check_id"], admin)
                    self.assertEqual(stale.exception.code, "MODEL_CHECK_STALE")
