"""Multilingual consent, recoverable stock errors and session-private memory."""

import unittest
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace
from uuid import uuid4

import httpx
from sqlalchemy import delete

from backend import chat
from backend.chat_language import action, select_language
from backend.chat_memory import search_history
from backend.assistant_contract import AssistantResult
from backend.db import get_engine, get_session_factory
from backend.main import app
from backend.models import Message, Session
from backend.sessions import COOKIE_NAME, resolve_session_from_cookie


class LanguageRulesTest(unittest.TestCase):
    def test_only_explicit_complete_consent(self):
        for text in ["Иә, қосыңыз!", "yes, add it", "иә добавь", "Да, добавь."]:
            self.assertEqual(action(text), "confirm", text)
        for text in ["да", "yes", "иә", "не добавляй", "yes add if cheaper", "да добавь 3", "иә қос бірақ 3", "yes add?", "он сказал да добавь"]:
            self.assertIsNone(action(text), text)

    def test_mixed_language_and_explicit_selection(self):
        self.assertEqual(select_language("Маған автомат 2 штуки керек"), "kk")
        self.assertEqual(select_language("Please покажи этот автомат"), "en")
        self.assertEqual(select_language("Need breaker", "kk", "ru"), "kk")
        self.assertEqual(select_language("Теперь answer in English", None, "ru"), "en")
        self.assertEqual(select_language("3", None, "kk"), "kk")


class ChatLanguagesTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.product = {"id": 21449, "name": "Test", "price": "100.00", "currency": "KZT",
                        "available_quantity": "10", "unit": "piece", "quantity_step": "1",
                        "checked_at": "2026-09-23T10:00:00Z"}
        self.catalog = SimpleNamespace(get_product=AsyncMock(side_effect=lambda *a, **kw: dict(self.product)))
        app.state.catalog = self.catalog
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
                                       headers={"Origin": "http://localhost:5173"})
        self.tokens = []

    async def asyncTearDown(self):
        await self.client.aclose()
        async with get_session_factory()() as db:
            for token in self.tokens:
                owner = await resolve_session_from_cookie(db, token)
                if owner:
                    await db.execute(delete(Session).where(Session.id == owner.id))
            await db.commit()
        await get_engine().dispose()

    async def session(self):
        self.client.cookies.clear()
        response = await self.client.post("/api/session", json={})
        self.assertEqual(response.status_code, 200)
        self.tokens.append(self.client.cookies.get(COOKIE_NAME))
        self.client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
        async with get_session_factory()() as db:
            return (await resolve_session_from_cookie(db, self.tokens[-1])).id

    async def send(self, text, language=None):
        response = await self.client.post("/api/chat/messages", json={"request_id": str(uuid4()), "text": text, "language": language})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    async def offer(self, quantity="2"):
        response = await self.client.post("/api/cart/proposals", json={"items": [{"product_id": 21449, "quantity": quantity}]})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    async def test_button_rejection_is_localized_and_saved_for_llm(self):
        owner_id = await self.session()
        async with get_session_factory()() as db:
            owner = await db.get(Session, owner_id)
            owner.preferred_language = "en"
            await db.commit()
        proposal = await self.offer()
        self.product["available_quantity"] = "1"
        response = await self.client.post(f"/api/cart/proposals/{proposal['id']}/confirm", json={"confirmed": True})
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn("at most 1", response.json()["error"]["message"])
        async with get_session_factory()() as db:
            state = (await db.get(Session, owner_id)).state
            self.assertEqual(state["last_product_ids"], [21449])
            self.assertEqual(state["last_cart_error"]["code"], "INSUFFICIENT_STOCK")
        self.assertEqual((await self.client.get("/api/cart")).json()["items"], [])

    async def test_kazakh_english_mixed_confirmation(self):
        for phrase, lang, expected in [("иә қосыңыз", "kk", "себетке"), ("yes add it", "en", "cart"), ("иә добавь", "kk", "себетке")]:
            await self.session()
            await self.offer()
            result = await self.send(phrase, lang)
            self.assertEqual(result["language"], lang)
            self.assertIn(expected, result["text"])
            self.assertEqual(result["cart"]["items"][0]["quantity"], "2")

    async def test_stock_drop_is_saved_reply_and_needs_new_confirmation(self):
        owner_id = await self.session()
        await self.offer()
        self.product["available_quantity"] = "1"
        result = await self.send("yes add it", "en")
        self.assertEqual(result["warnings"], ["INSUFFICIENT_STOCK"])
        self.assertIn("at most 1", result["text"])
        self.assertEqual(result["cart"]["items"], [])
        async with get_session_factory()() as db:
            self.assertEqual((await db.get(Session, owner_id)).state["last_cart_error"]["code"], "INSUFFICIENT_STOCK")
        result = await self.send("yes add it")
        self.assertIn("no active proposal", result["text"])
        await self.offer("1")
        result = await self.send("иә қосыңыз", "kk")
        self.assertEqual(result["cart"]["items"][0]["quantity"], "1")
        history = (await self.client.get("/api/chat/messages")).json()["items"]
        self.assertTrue(any("at most 1" in m["text"] for m in history))

    async def test_language_persists_and_ambiguous_yes_does_not_confirm(self):
        await self.session()
        await self.offer()
        seen = []
        async def reply(context, tools, emit):
            seen.append(context)
            result = AssistantResult(language=context.language, text="Қосуды растаңыз.")
            await emit({"type": "text.delta", "text": result.text})
            return result
        with patch.object(chat, "importlib", SimpleNamespace(import_module=lambda _: SimpleNamespace(reply=reply))):
            await self.send("Қазақ тілінде жауап бер")
            await self.send("yes")
        self.assertEqual(seen[-1].language, "kk")
        self.assertEqual((await self.client.get("/api/cart")).json()["items"], [])

    async def test_old_history_private_and_context_retained(self):
        owner = await self.session()
        async with get_session_factory()() as db:
            await chat._save_result(db, owner, uuid4(), uuid4(), {
                "language": "ru", "text": "Old breaker IEK discussed", "products": [{"id": 21449}],
                "proposal": None, "cart": None, "cart_url": None, "warnings": [],
            })
            for n in range(16):
                db.add(Message(session_id=owner, request_id=uuid4(), role="user", text=f"Recent note {n}", attachment_ids=[], status="completed"))
            await db.commit()
            recent = await chat._history(db, owner, uuid4())
            self.assertFalse(any("Old breaker" in m.text for m in recent))
            self.assertEqual((await db.get(Session, owner)).state["last_product_ids"], [21449])
        recalled = await search_history(get_session_factory(), owner, "IEK")
        self.assertEqual(recalled["items"][0]["product_ids"], [21449])
        other = await self.session()
        self.assertEqual((await search_history(get_session_factory(), other, "IEK"))["items"], [])


if __name__ == "__main__":
    unittest.main()
