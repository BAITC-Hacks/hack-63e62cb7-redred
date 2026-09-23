"""Focused account and explicit guest-cart merge checks against PostgreSQL."""

import asyncio
import unittest
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from backend.db import get_engine, get_session_factory
from backend.main import app
from backend.models import Session, User


class FakeCatalog:
    products = {
        21449: {"id": 21449, "name": "Автомат", "unit": "piece", "quantity_step": "1", "price": "100.00", "available_quantity": "10", "checked_at": "2026-09-23T12:00:00Z"},
        515280: {"id": 515280, "name": "Другой автомат", "unit": "piece", "quantity_step": "1", "price": "200.00", "available_quantity": "10", "checked_at": "2026-09-23T12:00:00Z"},
    }

    async def get_product(self, product_id: int, fresh: bool = False):
        if not fresh:
            raise AssertionError("Cart must use fresh product data")
        return dict(self.products[product_id])


class AuthFlowTest(unittest.TestCase):
    def setUp(self):
        self.email = f"auth-{uuid.uuid4().hex}@example.test"
        self.client_context = TestClient(app)
        self.client = self.client_context.__enter__()
        self.client.app.state.catalog = FakeCatalog()
        self.origin = {"Origin": "http://localhost:5173"}

    def tearDown(self):
        self.client_context.__exit__(None, None, None)

        async def remove_user():
            async with get_session_factory()() as db:
                user = await db.scalar(select(User).where(User.email == self.email))
                if user is not None:
                    session_id = user.session_id
                    await db.delete(user)
                    await db.flush()
                    await db.execute(delete(Session).where(Session.id == session_id))
                    await db.commit()
            await get_engine().dispose()

        asyncio.run(remove_user())

    def session(self):
        response = self.client.post("/api/session", json={}, headers=self.origin)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def mutate(self, path, body, csrf):
        return self.client.post(path, json=body, headers={**self.origin, "X-CSRF-Token": csrf})

    def add_item(self, product_id, csrf):
        proposal = self.mutate("/api/cart/proposals", {"items": [{"product_id": product_id, "quantity": "1"}]}, csrf)
        self.assertEqual(proposal.status_code, 200, proposal.text)
        confirmed = self.mutate(f"/api/cart/proposals/{proposal.json()['id']}/confirm", {"confirmed": True}, csrf)
        self.assertEqual(confirmed.status_code, 200, confirmed.text)

    def test_register_logout_and_login_keep_account_cart(self):
        guest = self.session()
        self.assertIsNone(guest["user"])
        self.add_item(21449, guest["csrf_token"])
        rejected = self.mutate("/api/auth/register", {"email": self.email, "password": "secret123", "role": "admin"}, guest["csrf_token"])
        self.assertEqual(rejected.status_code, 422)
        registered = self.mutate("/api/auth/register", {"email": self.email.upper(), "password": "secret123", "name": "Alice"}, guest["csrf_token"])
        self.assertEqual(registered.status_code, 200, registered.text)
        self.assertEqual(registered.json()["user"]["role"], "client")
        self.assertEqual(registered.json()["user"]["email"], self.email)
        self.assertEqual(self.session()["user"]["email"], self.email)
        self.assertEqual(self.client.get("/api/cart").json()["line_count"], 1)
        logout = self.mutate("/api/auth/logout", {}, registered.json()["csrf_token"])
        self.assertEqual(logout.status_code, 200, logout.text)
        self.assertFalse(self.client.get("/api/auth/me").json()["authenticated"])
        fresh = self.session()
        self.assertIsNone(fresh["user"])
        self.assertEqual(self.client.get("/api/cart").json()["line_count"], 0)
        wrong = self.mutate("/api/auth/login", {"email": self.email, "password": "wrongpass"}, fresh["csrf_token"])
        self.assertEqual(wrong.status_code, 401)
        logged_in = self.mutate("/api/auth/login", {"email": self.email, "password": "secret123"}, fresh["csrf_token"])
        self.assertEqual(logged_in.status_code, 200, logged_in.text)
        self.assertEqual(self.client.get("/api/cart").json()["items"][0]["product_id"], 21449)

    def test_two_nonempty_carts_require_explicit_proposal(self):
        guest = self.session()
        self.add_item(21449, guest["csrf_token"])
        registered = self.mutate("/api/auth/register", {"email": self.email, "password": "secret123"}, guest["csrf_token"])
        self.assertEqual(registered.status_code, 200, registered.text)
        self.assertEqual(self.mutate("/api/auth/logout", {}, registered.json()["csrf_token"]).status_code, 200)
        second_guest = self.session()
        self.add_item(515280, second_guest["csrf_token"])
        logged_in = self.mutate("/api/auth/login", {"email": self.email, "password": "secret123"}, second_guest["csrf_token"])
        self.assertEqual(logged_in.status_code, 200, logged_in.text)
        draft = logged_in.json()["cart_merge_draft"]
        self.assertIsNotNone(draft)
        self.assertEqual(self.client.get("/api/cart").json()["line_count"], 1)
        self.assertEqual(self.client.get("/api/auth/cart-drafts").json()["items"][0]["id"], draft["id"])
        prepared = self.mutate(f"/api/auth/cart-drafts/{draft['id']}/proposal", {}, logged_in.json()["csrf_token"])
        self.assertEqual(prepared.status_code, 200, prepared.text)
        proposal_id = prepared.json()["proposal"]["id"]
        self.assertEqual(self.client.get("/api/cart").json()["line_count"], 1)
        confirmed = self.mutate(f"/api/cart/proposals/{proposal_id}/confirm", {"confirmed": True}, logged_in.json()["csrf_token"])
        self.assertEqual(confirmed.status_code, 200, confirmed.text)
        self.assertEqual(self.client.get("/api/cart").json()["line_count"], 2)
        repeated = self.mutate(f"/api/auth/cart-drafts/{draft['id']}/proposal", {}, logged_in.json()["csrf_token"])
        self.assertEqual(repeated.status_code, 200, repeated.text)
        self.assertEqual(repeated.json()["proposal"]["id"], proposal_id)
        self.assertEqual(repeated.json()["draft"]["status"], "confirmed")
        self.assertEqual(self.client.get("/api/cart").json()["line_count"], 2)


if __name__ == "__main__":
    unittest.main()
