"""Admin workspace checks against PostgreSQL, with a fake background service."""

import os
from datetime import timedelta
from decimal import Decimal
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi import FastAPI
import httpx
from sqlalchemy import delete, select

from backend.admin import router
from backend.config import get_settings
from backend.db import get_session_factory
from backend.errors import APIError, api_error_handler
from backend.models import (
    Cart, CartItem, CartProposal, Message, RuntimeSetting, Session, User, utcnow,
)
from backend.runtime_settings import read_settings


class FakeSync:
    def __init__(self):
        self.reload_count = 0

    async def reload(self):
        self.reload_count += 1
        return {}


class AdminWorkspaceTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.env_patch = patch.dict(os.environ, {
            "ADMIN_PASSWORD": "admin-workspace-test", "ADMIN_ORIGIN": "http://localhost:8000",
            "CATALOG_SYNC_ENABLED": "false",
        })
        self.env_patch.start()
        get_settings.cache_clear()
        self.sync = FakeSync()
        app = FastAPI()
        app.add_exception_handler(APIError, api_error_handler)
        app.include_router(router)
        app.state.catalog_sync = self.sync
        self.app = app
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test",
            headers={"Origin": "http://localhost:8000"},
        )
        self.created_sessions = []
        self.created_user = None
        async with get_session_factory()() as db:
            self.previous_setting = await db.get(RuntimeSetting, "catalog_sync_interval_seconds")
            self.previous_setting_value = self.previous_setting.value.copy() if self.previous_setting else None

    async def asyncTearDown(self):
        await self.client.aclose()
        async with get_session_factory()() as db:
            if self.created_user:
                await db.execute(delete(User).where(User.id == self.created_user))
            for identifier in self.created_sessions:
                await db.execute(delete(Session).where(Session.id == identifier))
            if self.previous_setting_value is None:
                await db.execute(delete(RuntimeSetting).where(RuntimeSetting.key == "catalog_sync_interval_seconds"))
            else:
                row = await db.get(RuntimeSetting, "catalog_sync_interval_seconds")
                row.value = self.previous_setting_value
            await db.commit()
        self.env_patch.stop()
        get_settings.cache_clear()

    async def _seed(self):
        async with get_session_factory()() as db:
            guest = Session(token_hash="guest-" + uuid4().hex, csrf_token=uuid4().hex,
                            state={}, expires_at=utcnow() + timedelta(hours=1))
            client = Session(token_hash="client-" + uuid4().hex, csrf_token=uuid4().hex,
                             state={}, expires_at=utcnow() + timedelta(hours=1))
            db.add_all([guest, client])
            await db.flush()
            self.created_sessions = [guest.id, client.id]
            user = User(email=f"admin-workspace-{uuid4().hex}@example.test", password_hash="private-hash",
                        name="Проверочный клиент", role="client", session_id=client.id)
            db.add(user)
            await db.flush()
            self.created_user = user.id
            db.add_all([
                Message(session_id=guest.id, request_id=uuid4(), role="user", text="Тестовый гостевой вопрос",
                        attachment_ids=[], status="completed"),
                Message(session_id=client.id, request_id=uuid4(), role="user", text="Тестовый клиентский вопрос",
                        attachment_ids=[str(uuid4())], status="completed"),
            ])
            cart = Cart(session_id=client.id, revision=1)
            db.add(cart)
            await db.flush()
            db.add(CartItem(cart_id=cart.id, product_id=21449, quantity=Decimal("1"),
                            unit_price=Decimal("100.00"), name="Тестовый товар", unit="piece",
                            price_checked_at=utcnow()))
            db.add(CartProposal(session_id=client.id, cart_id=cart.id, base_revision=0,
                                items=[{"product_id": 21449, "quantity": "1", "unit_price": "100.00", "name": "Тестовый товар", "unit": "piece"}],
                                expires_at=utcnow() + timedelta(minutes=10), status="confirmed"))
            await db.commit()
            return guest.id, client.id, user.email

    async def test_workspace_counts_private_chats_and_settings(self):
        # Neither guest nor client cookies grant admin access.
        visitor = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://test",
                                    headers={"Origin": "http://localhost:8000"},
                                    cookies={"hackalem_session": "guest", "hackalem_client": "client"})
        try:
            self.assertEqual((await visitor.get("/api/admin/dashboard")).status_code, 401)
            self.assertEqual((await visitor.get("/api/admin/chats")).status_code, 401)
            self.assertEqual((await visitor.get("/api/admin/settings")).status_code, 401)
        finally:
            await visitor.aclose()
        page = await self.client.get("/admin")
        self.assertEqual(page.status_code, 200)
        self.assertIn('id="chats-tab"', page.text)

        login = await self.client.post("/api/admin/session", json={"password": "admin-workspace-test"})
        self.assertEqual(login.status_code, 200, login.text)
        self.assertEqual(login.json()["role"], "admin")
        csrf = login.json()["csrf_token"]
        baseline = (await self.client.get("/api/admin/dashboard")).json()["counts"]
        guest_id, client_id, email = await self._seed()
        counts = (await self.client.get("/api/admin/dashboard")).json()["counts"]
        for key, delta in {
            "registered_clients": 1, "active_guests": 1, "conversations": 2,
            "messages": 2, "cart_items": 1, "confirmed_proposals": 1,
        }.items():
            self.assertEqual(counts[key], baseline[key] + delta, key)
        self.assertEqual(counts["catalog_products"], baseline["catalog_products"])

        clients = await self.client.get("/api/admin/chats", params={"kind": "client", "q": email, "limit": 1})
        self.assertEqual(clients.status_code, 200, clients.text)
        self.assertEqual(clients.json()["total"], 1)
        self.assertEqual(clients.json()["items"][0]["session_id"], str(client_id))
        self.assertEqual(clients.json()["items"][0]["user"]["role"], "client")
        guests = await self.client.get("/api/admin/chats", params={"kind": "guest", "q": str(guest_id)})
        self.assertEqual(guests.json()["items"][0]["session_id"], str(guest_id))
        messages = await self.client.get(f"/api/admin/chats/{client_id}/messages", params={"limit": 1})
        self.assertEqual(messages.status_code, 200, messages.text)
        self.assertEqual(messages.json()["total"], 1)
        self.assertEqual(messages.json()["items"][0]["attachment_count"], 1)
        self.assertNotIn("private-hash", messages.text)
        self.assertNotIn("token_hash", messages.text)
        self.assertNotIn("storage_path", messages.text)

        forbidden = await self.client.patch("/api/admin/settings", json={"catalog_sync_interval_seconds": 123})
        self.assertEqual(forbidden.status_code, 403)
        headers = {"X-CSRF-Token": csrf}
        invalid = await self.client.patch("/api/admin/settings", json={"unlisted": True}, headers=headers)
        self.assertEqual(invalid.status_code, 422)
        self.assertEqual(self.sync.reload_count, 0)
        saved = await self.client.patch("/api/admin/settings", json={"catalog_sync_interval_seconds": 123}, headers=headers)
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual(saved.json()["catalog_sync_interval_seconds"], 123)
        self.assertEqual(self.sync.reload_count, 1)
        async with get_session_factory()() as db:
            self.assertEqual((await read_settings(db))["catalog_sync_interval_seconds"], 123)
