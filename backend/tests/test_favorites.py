"""Focused HTTP checks for session-owned favorites, with no external catalog calls."""

import unittest

import httpx
from fastapi import FastAPI
from sqlalchemy import delete, select

from backend import favorites, sessions
from backend.config import get_settings
from backend.db import get_engine, get_session_factory
from backend.errors import APIError, api_error_handler
from backend.models import ProductSnapshot, Session


PRODUCT_ID = 9930001


class FavoritesTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.session_ids = []
        app = FastAPI()
        app.add_exception_handler(APIError, api_error_handler)
        app.include_router(sessions.router)
        app.include_router(favorites.router)
        self.app = app
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")
        async with get_session_factory()() as db:
            await db.merge(ProductSnapshot(
                id=PRODUCT_ID, article="FAV_1", supplier_article="",
                normalized={"id": PRODUCT_ID, "name": "Проверочный товар", "price": "10.00"},
                raw={"id": PRODUCT_ID, "name": "Проверочный товар"},
            ))
            await db.commit()

    async def asyncTearDown(self):
        await self.client.aclose()
        async with get_session_factory()() as db:
            if self.session_ids:
                await db.execute(delete(Session).where(Session.id.in_(self.session_ids)))
            await db.execute(delete(ProductSnapshot).where(ProductSnapshot.id == PRODUCT_ID))
            await db.commit()
        await get_engine().dispose()

    async def _session(self, client):
        response = await client.post("/api/session", headers={"Origin": get_settings().app_origin})
        self.assertEqual(response.status_code, 200)
        token = client.cookies.get(sessions.COOKIE_NAME)
        async with get_session_factory()() as db:
            row = await db.scalar(select(Session).where(Session.token_hash == sessions.token_hash(token)))
            self.session_ids.append(row.id)
        return response.json()["csrf_token"]

    async def test_idempotence_owner_isolation_csrf_and_missing_product(self):
        csrf = await self._session(self.client)
        headers = {"Origin": get_settings().app_origin, "X-CSRF-Token": csrf}
        missing_csrf = await self.client.put(f"/api/favorites/{PRODUCT_ID}", headers={"Origin": get_settings().app_origin})
        self.assertEqual(missing_csrf.status_code, 403)
        for _ in range(2):
            added = await self.client.put(f"/api/favorites/{PRODUCT_ID}", headers=headers)
            self.assertEqual(added.status_code, 200)
            self.assertTrue(added.json()["favorite"])
        listed = await self.client.get("/api/favorites?limit=1&offset=0")
        self.assertEqual((listed.json()["total"], len(listed.json()["items"])), (1, 1))
        self.assertEqual(listed.json()["items"][0]["id"], PRODUCT_ID)
        self.assertEqual((await self.client.get("/api/favorites?limit=1&offset=1")).json()["items"], [])
        self.assertEqual((await self.client.put("/api/favorites/99999999", headers=headers)).status_code, 404)

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://testserver") as other:
            await self._session(other)
            self.assertEqual((await other.get("/api/favorites")).json()["total"], 0)

        for _ in range(2):
            removed = await self.client.delete(f"/api/favorites/{PRODUCT_ID}", headers=headers)
            self.assertEqual(removed.status_code, 200)
            self.assertFalse(removed.json()["favorite"])
        self.assertEqual((await self.client.get("/api/favorites")).json()["total"], 0)


if __name__ == "__main__":
    unittest.main()
