"""Focused HTTP cart mutation checks with PostgreSQL and a controlled catalog."""

import unittest

from fastapi import FastAPI
import httpx
from sqlalchemy import delete

from backend import cart, sessions
from backend.db import get_engine, get_session_factory
from backend.errors import APIError, api_error_handler
from backend.models import Session
from backend.sessions import COOKIE_NAME, resolve_session_from_cookie


class ControlledCatalog:
    def __init__(self):
        self.calls = 0
        self.products = {
            1001: {"id": 1001, "name": "Первый", "unit": "piece", "quantity_step": "1", "price": "100.00", "available_quantity": "10", "checked_at": "2026-09-23T12:00:00Z"},
            1002: {"id": 1002, "name": "Второй", "unit": "piece", "quantity_step": "1", "price": "50.00", "available_quantity": "10", "checked_at": "2026-09-23T12:00:00Z"},
        }

    async def get_product(self, product_id: int, fresh: bool = False):
        if not fresh:
            raise AssertionError("Cart must use fresh catalog details")
        self.calls += 1
        return dict(self.products[product_id])


class CartMutationsTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        app = FastAPI()
        app.add_exception_handler(APIError, api_error_handler)
        app.include_router(sessions.router)
        app.include_router(cart.router)
        self.catalog = ControlledCatalog()
        app.state.catalog = self.catalog
        self.clients = []
        self.session_tokens = []
        self.app = app
        self.client, self.headers = await self._new_client()

    async def asyncTearDown(self):
        for client in self.clients:
            await client.aclose()
        async with get_session_factory()() as db:
            for token in self.session_tokens:
                owner = await resolve_session_from_cookie(db, token)
                if owner is not None:
                    await db.execute(delete(Session).where(Session.id == owner.id))
            await db.commit()
        await get_engine().dispose()

    async def _new_client(self):
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://test",
            headers={"Origin": "http://localhost:5173"},
        )
        self.clients.append(client)
        result = await client.post("/api/session", json={})
        self.assertEqual(result.status_code, 200, result.text)
        self.session_tokens.append(client.cookies.get(COOKIE_NAME))
        return client, {"X-CSRF-Token": result.json()["csrf_token"]}

    async def _proposal(self, items):
        response = await self.client.post("/api/cart/proposals", json={"items": items}, headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    async def _confirm(self, identifier):
        return await self.client.post(f"/api/cart/proposals/{identifier}/confirm", json={"confirmed": True}, headers=self.headers)

    async def _delete(self, path, body):
        return await self.client.request("DELETE", path, json=body, headers=self.headers)

    async def test_confirm_update_delete_clear_and_failure_invariants(self):
        proposal = await self._proposal([
            {"product_id": 1001, "quantity": "2"}, {"product_id": 1002, "quantity": "1"},
        ])
        confirmed = await self._confirm(proposal["id"])
        self.assertEqual(confirmed.status_code, 200, confirmed.text)
        self.assertEqual(confirmed.json()["cart"]["revision"], 1)
        self.assertEqual(confirmed.json()["cart"]["total"], "250.00")

        changed = await self.client.patch(
            "/api/cart/items/1001", json={"confirmed": True, "quantity": "3", "expected_revision": 1},
            headers=self.headers,
        )
        self.assertEqual(changed.status_code, 200, changed.text)
        self.assertEqual(changed.json()["revision"], 2)
        self.assertEqual(changed.json()["total"], "350.00")
        self.assertEqual(changed.json()["items"][0]["quantity"], "3")
        stable = changed.json()

        for quantity in ("0", "-1", "NaN", "Infinity", "1.1"):
            with self.subTest(quantity=quantity):
                failed = await self.client.patch(
                    "/api/cart/items/1001", json={"confirmed": True, "quantity": quantity, "expected_revision": 2},
                    headers=self.headers,
                )
                self.assertEqual(failed.status_code, 422, failed.text)
        self.assertEqual((await self.client.get("/api/cart")).json(), stable)

        self.catalog.products[1001]["available_quantity"] = "2"
        stock = await self.client.patch(
            "/api/cart/items/1001", json={"confirmed": True, "quantity": "4", "expected_revision": 2},
            headers=self.headers,
        )
        self.assertEqual(stock.status_code, 409, stock.text)
        self.catalog.products[1001]["available_quantity"] = "10"
        self.catalog.products[1001]["price"] = "120.00"
        price = await self.client.patch(
            "/api/cart/items/1001", json={"confirmed": True, "quantity": "2", "expected_revision": 2},
            headers=self.headers,
        )
        self.assertEqual(price.status_code, 409, price.text)
        self.catalog.products[1001]["price"] = "100.00"
        self.assertEqual((await self.client.get("/api/cart")).json(), stable)

        for confirmation in (False, "true", 1, None):
            with self.subTest(confirmation=confirmation):
                body = {"quantity": "2", "expected_revision": 2}
                if confirmation is not None:
                    body["confirmed"] = confirmation
                failed = await self.client.patch("/api/cart/items/1001", json=body, headers=self.headers)
                self.assertEqual(failed.status_code, 422, failed.text)
                delete_body = {"expected_revision": 2}
                if confirmation is not None:
                    delete_body["confirmed"] = confirmation
                for path in ("/api/cart/items/1001", "/api/cart"):
                    failed_delete = await self._delete(path, delete_body)
                    self.assertEqual(failed_delete.status_code, 422, failed_delete.text)
        no_csrf = await self.client.patch(
            "/api/cart/items/1001", json={"confirmed": True, "quantity": "2"},
        )
        self.assertEqual(no_csrf.status_code, 403)
        stale_revision = await self.client.patch(
            "/api/cart/items/1001", json={"confirmed": True, "quantity": "2", "expected_revision": 1},
            headers=self.headers,
        )
        self.assertEqual(stale_revision.status_code, 409)
        self.assertEqual((await self.client.get("/api/cart")).json(), stable)

        pending = await self._proposal([{"product_id": 1002, "quantity": "1"}])
        calls_before_delete = self.catalog.calls
        removed = await self._delete("/api/cart/items/1001", {"confirmed": True, "expected_revision": 2})
        self.assertEqual(removed.status_code, 200, removed.text)
        self.assertEqual(removed.json()["revision"], 3)
        self.assertEqual(removed.json()["total"], "50.00")
        self.assertEqual(self.catalog.calls, calls_before_delete)
        old_pending = await self._confirm(pending["id"])
        self.assertEqual(old_pending.status_code, 409)
        replay = await self._confirm(proposal["id"])
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertEqual(replay.json()["cart"], removed.json())
        self.assertEqual((await self.client.get("/api/cart")).json(), removed.json())

        repeat_delete = await self._delete("/api/cart/items/1001", {"confirmed": True, "expected_revision": 3})
        self.assertEqual(repeat_delete.status_code, 200, repeat_delete.text)
        self.assertEqual(repeat_delete.json(), removed.json())

        other, other_headers = await self._new_client()
        self.assertEqual((await other.get("/api/cart")).json()["items"], [])
        foreign_clear = await other.request("DELETE", "/api/cart", json={"confirmed": True}, headers=other_headers)
        self.assertEqual(foreign_clear.status_code, 200, foreign_clear.text)
        self.assertEqual((await self.client.get("/api/cart")).json(), removed.json())

        calls_before_clear = self.catalog.calls
        cleared = await self._delete("/api/cart", {"confirmed": True, "expected_revision": 3})
        self.assertEqual(cleared.status_code, 200, cleared.text)
        self.assertEqual(cleared.json()["revision"], 4)
        self.assertEqual(cleared.json()["items"], [])
        self.assertEqual(cleared.json()["line_count"], 0)
        self.assertEqual(cleared.json()["total"], "0.00")
        self.assertEqual(self.catalog.calls, calls_before_clear)
        repeated_clear = await self._delete("/api/cart", {"confirmed": True, "expected_revision": 4})
        self.assertEqual(repeated_clear.status_code, 200, repeated_clear.text)
        self.assertEqual(repeated_clear.json(), cleared.json())
