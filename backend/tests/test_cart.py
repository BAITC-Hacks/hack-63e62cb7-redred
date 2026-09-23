"""Focused PostgreSQL checks for cart confirmation invariants.

Requires DATABASE_URL pointing to a migrated, disposable development database.
"""

import asyncio
import unittest
import uuid
from datetime import timedelta
from types import SimpleNamespace

from sqlalchemy import delete

from backend.cart import confirm_proposal, create_proposal, get_cart_payload
from backend.db import get_engine, get_session_factory
from backend.errors import APIError
from backend.models import Cart, Session, utcnow


class ControlledCatalog:
    def __init__(self):
        self.products = {
            1001: {"id": 1001, "name": "Первый", "unit": "piece", "quantity_step": "1", "price": "100.00", "available_quantity": "10", "checked_at": "2026-09-23T12:00:00Z"},
            1002: {"id": 1002, "name": "Второй", "unit": "piece", "quantity_step": "1", "price": "50.00", "available_quantity": "10", "checked_at": "2026-09-23T12:00:00Z"},
        }

    async def get_product(self, product_id: int, fresh: bool = False):
        if not fresh:
            raise AssertionError("Cart must request fresh catalog data")
        return dict(self.products[product_id])


class CartConfirmationTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.session_id = uuid.uuid4()
        self.owner = SimpleNamespace(id=self.session_id)
        self.catalog = ControlledCatalog()
        async with get_session_factory()() as db:
            db.add(Session(
                id=self.session_id, token_hash=uuid.uuid4().hex + uuid.uuid4().hex,
                csrf_token=uuid.uuid4().hex, state={}, expires_at=utcnow() + timedelta(hours=1),
            ))
            await db.flush()
            db.add(Cart(id=uuid.uuid4(), session_id=self.session_id, revision=0))
            await db.commit()

    async def asyncTearDown(self):
        async with get_session_factory()() as db:
            await db.execute(delete(Session).where(Session.id == self.session_id))
            await db.commit()
        await get_engine().dispose()

    async def _proposal(self, items):
        async with get_session_factory()() as db:
            return await create_proposal(db, self.owner, items, self.catalog)

    async def _confirm(self, proposal_id):
        async with get_session_factory()() as db:
            return await confirm_proposal(db, self.owner, proposal_id, self.catalog)

    async def _cart(self):
        async with get_session_factory()() as db:
            return await get_cart_payload(db, self.owner)

    async def test_concurrent_confirmation_is_idempotent(self):
        proposal = await self._proposal([{"product_id": 1001, "quantity": "2"}])
        first, second = await asyncio.gather(self._confirm(proposal["id"]), self._confirm(proposal["id"]))
        self.assertEqual(first, second)
        cart = await self._cart()
        self.assertEqual(cart["revision"], 1)
        self.assertEqual(cart["items"][0]["quantity"], "2")
        self.assertEqual(cart["total"], "200.00")

    async def test_multi_item_confirmation_is_all_or_nothing(self):
        proposal = await self._proposal([
            {"product_id": 1001, "quantity": "2"},
            {"product_id": 1002, "quantity": "3"},
        ])
        self.catalog.products[1002]["available_quantity"] = "2"
        with self.assertRaises(APIError) as caught:
            await self._confirm(proposal["id"])
        self.assertEqual(caught.exception.code, "INSUFFICIENT_STOCK")
        cart = await self._cart()
        self.assertEqual(cart["revision"], 0)
        self.assertEqual(cart["items"], [])

    async def test_price_change_rejects_confirmation(self):
        proposal = await self._proposal([{"product_id": 1001, "quantity": "1"}])
        self.catalog.products[1001]["price"] = "120.00"
        with self.assertRaises(APIError) as caught:
            await self._confirm(proposal["id"])
        self.assertEqual(caught.exception.code, "PRICE_CHANGED")
        self.assertEqual((await self._cart())["items"], [])


if __name__ == "__main__":
    unittest.main()
