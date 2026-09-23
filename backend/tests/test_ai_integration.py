"""Admin probe/activation and chat fallback with PostgreSQL and fake OpenAI HTTP."""

import json
import os
import secrets
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from backend.config import get_settings
from backend.db import get_session_factory
from backend.models import AdminSession, RuntimeSetting, Session
from backend.sessions import token_hash


class AIIntegrationTest(unittest.TestCase):
    def test_checked_activation_then_chat_fallback_and_outage(self):
        default_model = "gpt-6-sol"
        chosen_model = "gpt-4.1-mini"
        provider_requests = []
        created_sessions = set()
        admin_hashes = set()
        saved_settings = []
        fake_client = None
        original_control = None
        settings_filter = RuntimeSetting.key.like("assistant.%")

        def response_data(language, text):
            result = {"language": language, "text": text, "product_ids": [], "proposed_items": [], "warnings": []}
            raw = json.dumps(result, ensure_ascii=False)
            return {"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": raw}]}]}

        def provider(request):
            provider_requests.append(request.url.path)
            self.assertEqual(request.headers.get("Authorization"), "Bearer integration-test-key")
            if request.url.path == "/v1/models":
                return httpx.Response(200, json={"data": [{"id": name} for name in (default_model, chosen_model, "text-embedding-3-small")]})
            self.assertEqual(request.url.path, "/v1/responses")
            payload = json.loads(request.content)
            self.assertEqual(payload["model"], chosen_model)
            if not payload.get("stream"):
                return httpx.Response(200, json={"status": "completed", "output": [{
                    "type": "function_call", "name": "get_cart", "arguments": "{}", "call_id": "call_1",
                }]})
            completed = response_data("en", "Capability check passed")
            events = [
                {"type": "response.output_text.delta", "delta": completed["output"][0]["content"][0]["text"]},
                {"type": "response.completed", "response": completed},
            ]
            stream = "".join("data: " + json.dumps(event) + "\n\n" for event in events)
            return httpx.Response(200, text=stream, headers={"Content-Type": "text/event-stream"})

        async def install(app):
            nonlocal fake_client, original_control
            from backend.ai_control import AIModelControl

            async with get_session_factory()() as db:
                rows = (await db.scalars(select(RuntimeSetting).where(settings_filter))).all()
                saved_settings.extend({"key": row.key, "value": row.value, "updated_at": row.updated_at} for row in rows)
                await db.execute(delete(RuntimeSetting).where(settings_filter))
                await db.commit()
            fake_client = httpx.AsyncClient(transport=httpx.MockTransport(provider))
            original_control = app.state.ai_models
            control = AIModelControl(get_session_factory(), fake_client)
            app.state.ai_models = control
            app.state.catalog.ai_model_control = control

        async def remember_guest(token):
            async with get_session_factory()() as db:
                identifier = await db.scalar(select(Session.id).where(Session.token_hash == token_hash(token)))
                created_sessions.add(identifier)

        async def cleanup(app):
            async with get_session_factory()() as db:
                await db.execute(delete(RuntimeSetting).where(settings_filter))
                db.add_all(RuntimeSetting(**row) for row in saved_settings)
                await db.execute(delete(Session).where(Session.id.in_(created_sessions)))
                await db.execute(delete(AdminSession).where(AdminSession.token_hash.in_(admin_hashes)))
                await db.commit()
            if fake_client:
                await fake_client.aclose()
            app.state.ai_models = original_control
            app.state.catalog.ai_model_control = original_control

        environment = {
            "OPENAI_API_KEY": "integration-test-key", "OPENAI_MODEL": default_model,
            "ADMIN_PASSWORD": secrets.token_urlsafe(24), "ADMIN_ORIGIN": "http://localhost:8000",
            "APP_ORIGIN": "http://localhost:5173", "CATALOG_SYNC_ENABLED": "false",
            "EKT_API_USERNAME": "", "EKT_API_PASSWORD": "",
        }
        with patch.dict(os.environ, environment):
            get_settings.cache_clear()
            from backend.main import app
            from backend.assistant import service
            from backend.ai_provider import AIProviderFailure

            with TestClient(app, base_url=environment["ADMIN_ORIGIN"]) as client:
                client.portal.call(install, app)
                try:
                    login = client.post("/api/admin/session", headers={"Origin": environment["ADMIN_ORIGIN"]}, json={"password": environment["ADMIN_PASSWORD"]})
                    self.assertEqual(login.status_code, 200, login.text)
                    admin_hashes.add(token_hash(client.cookies.get("hackalem_admin")))
                    headers = {"Origin": environment["ADMIN_ORIGIN"], "X-CSRF-Token": login.json()["csrf_token"]}
                    models = client.get("/api/admin/ai/models")
                    self.assertEqual(models.status_code, 200, models.text)
                    self.assertEqual(set(models.json()["models"]), {default_model, chosen_model})
                    checked = client.post("/api/admin/ai/check", headers=headers, json={"model": chosen_model})
                    self.assertEqual(checked.status_code, 200, checked.text)
                    self.assertEqual(client.get("/api/admin/ai/status").json()["active_model"], default_model)
                    activated = client.post("/api/admin/ai/activate", headers=headers, json={"model": chosen_model, "check_id": checked.json()["check_id"]})
                    self.assertEqual(activated.status_code, 200, activated.text)
                    self.assertEqual(activated.json()["active_model"], chosen_model)
                    self.assertEqual(provider_requests.count("/v1/responses"), 2)

                    guest = client.post("/api/session", headers={"Origin": environment["APP_ORIGIN"]})
                    self.assertEqual(guest.status_code, 200, guest.text)
                    client.portal.call(remember_guest, client.cookies.get("hackalem_session"))
                    chat_headers = {"Origin": environment["APP_ORIGIN"], "X-CSRF-Token": guest.json()["csrf_token"]}
                    attempted_models = []

                    async def runtime_request(_client, payload, *args, **kwargs):
                        attempted_models.append(payload["model"])
                        if payload["model"] == chosen_model:
                            raise AIProviderFailure("server_error", "OpenAI temporarily unavailable", True, 503)
                        return response_data("ru", "Ответ резервной модели")

                    with patch.object(service, "_request", side_effect=runtime_request):
                        answer = client.post("/api/chat/messages", headers=chat_headers, json={"request_id": str(uuid4()), "text": "Помоги выбрать товар", "language": "ru"})
                    self.assertEqual(answer.status_code, 200, answer.text)
                    self.assertEqual(attempted_models, [chosen_model, default_model])
                    status = client.get("/api/admin/ai/status").json()
                    self.assertEqual(status["active_model"], default_model)
                    self.assertIsNone(status["override_model"])
                    self.assertEqual(status["health"], "healthy")
                    self.assertEqual(status["last_incident"]["fallback_to"], default_model)

                    failure = AIProviderFailure("server_error", "Do not expose this internal detail", True, 503)
                    with patch.object(service, "_request", AsyncMock(side_effect=failure)) as unavailable:
                        answer = client.post("/api/chat/messages", headers=chat_headers, json={"request_id": str(uuid4()), "text": "Помоги снова", "language": "ru"})
                    self.assertEqual(answer.status_code, 503, answer.text)
                    self.assertIn("временно недоступен", answer.json()["error"]["message"])
                    self.assertNotIn("internal detail", answer.text)
                    self.assertEqual(unavailable.await_count, 1)
                    self.assertEqual(client.get("/api/admin/ai/status").json()["health"], "unavailable")
                finally:
                    client.portal.call(cleanup, app)
            get_settings.cache_clear()
