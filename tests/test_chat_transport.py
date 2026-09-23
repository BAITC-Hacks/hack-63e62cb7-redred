"""Focused in-process transport check; the assistant here is a test fixture only."""

from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import uuid4

import httpx
from sqlalchemy import delete

from backend import chat
from backend.assistant_contract import AssistantResult, ProposedItem
from backend.db import get_engine, get_session_factory
from backend.main import app
from backend.models import Session
from backend.sessions import COOKIE_NAME, resolve_session_from_cookie


class FakeCatalog:
    def __init__(self):
        self.calls = 0

    async def get_product(self, product_id, *, fresh=False):
        self.calls += 1
        assert fresh is True
        assert product_id == 21449
        return {
            "id": 21449, "name": "Проверочный товар", "price": "100.00",
            "currency": "KZT", "available_quantity": "10", "unit": "piece",
            "quantity_step": "1", "checked_at": "2026-09-23T09:00:00Z",
        }


class ChatTransportTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.catalog = FakeCatalog()
        app.state.catalog = self.catalog
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test",
            headers={"Origin": "http://localhost:5173"},
        )
        self.tokens = []

    async def asyncTearDown(self):
        await self.client.aclose()
        async with get_session_factory()() as db:
            for token in self.tokens:
                session = await resolve_session_from_cookie(db, token)
                if session is not None:
                    await db.execute(delete(Session).where(Session.id == session.id))
            await db.commit()
        await get_engine().dispose()

    async def _session(self):
        response = await self.client.post("/api/session", json={})
        self.assertEqual(response.status_code, 200, response.text)
        self.tokens.append(self.client.cookies.get(COOKIE_NAME))
        return {"X-CSRF-Token": response.json()["csrf_token"]}

    async def test_offer_replay_stream_confirmation_and_attachment_owner(self):
        headers = await self._session()
        calls = 0

        async def fake_reply(context, tools, emit):
            nonlocal calls
            calls += 1
            self.assertEqual((await tools.get_product(21449))["id"], 21449)
            self.assertEqual((await tools.get_product(21449))["id"], 21449)
            await emit({"type": "text.delta", "text": "Нашёл "})
            await emit({"type": "text.delta", "text": "товар."})
            return AssistantResult(
                language="ru", text="Нашёл товар.", product_ids=[21449],
                proposed_items=[ProposedItem(product_id=21449, quantity="2")],
            )

        body = {"request_id": str(uuid4()), "text": "Нужен товар", "attachment_ids": []}
        with patch.object(chat, "importlib", SimpleNamespace(import_module=lambda _: SimpleNamespace(reply=fake_reply))):
            response = await self.client.post("/api/chat/messages", json=body, headers=headers)
            self.assertEqual(response.status_code, 200, response.text)
            result = response.json()
            self.assertEqual(result["proposal"]["items"][0]["quantity"], "2")
            self.assertEqual((await self.client.get("/api/cart")).json()["revision"], 0)
            replay = await self.client.post("/api/chat/messages", json=body, headers=headers)
            self.assertEqual(replay.json(), result)
            self.assertEqual(calls, 1)
            conflict = await self.client.post("/api/chat/messages", json={**body, "text": "другое"}, headers=headers)
            self.assertEqual(conflict.status_code, 409)
            self.assertEqual(conflict.json()["error"]["code"], "REQUEST_ID_CONFLICT")

            async with get_session_factory()() as db:
                session = await resolve_session_from_cookie(db, self.tokens[0])
                events = []

                async def publish(kind, data):
                    events.append((kind, data))

                second = chat.MessageInput(request_id=uuid4(), text="Ещё товар")
                await chat.handle_message(db, session, second, self.catalog, publish)
                kinds = [kind for kind, _ in events]
                self.assertLess(kinds.index("message.accepted"), kinds.index("assistant.delta"))
                self.assertLess(kinds.index("assistant.delta"), kinds.index("assistant.completed"))
                self.assertEqual("".join(data["text"] for kind, data in events if kind == "assistant.delta"), "Нашёл товар.")

        confirm = {"request_id": str(uuid4()), "text": "да, добавь", "attachment_ids": []}
        response = await self.client.post("/api/chat/messages", json=confirm, headers=headers)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["cart"]["revision"], 1)
        replay = await self.client.post("/api/chat/messages", json=confirm, headers=headers)
        self.assertEqual(replay.json()["cart"]["revision"], 1)
        self.assertEqual((await self.client.get("/api/cart")).json()["items"][0]["quantity"], "2")

        upload = await self.client.post(
            "/api/attachments", files={"file": ("sample.pdf", b"%PDF-1.4\n%%EOF", "application/pdf")}, headers=headers,
        )
        self.assertEqual(upload.status_code, 200, upload.text)
        attachment_id = upload.json()["attachment_id"]
        self.assertEqual((await self.client.get(f"/api/attachments/{attachment_id}")).status_code, 200)
        other = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", headers={"Origin": "http://localhost:5173"})
        try:
            self.assertEqual((await other.post("/api/session", json={})).status_code, 200)
            self.tokens.append(other.cookies.get(COOKIE_NAME))
            foreign = await other.get(f"/api/attachments/{attachment_id}")
            self.assertEqual(foreign.status_code, 404)
        finally:
            await other.aclose()
