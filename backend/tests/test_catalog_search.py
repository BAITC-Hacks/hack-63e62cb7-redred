"""Focused PostgreSQL checks for catalog filters and paginated ordering.

Run against a disposable migrated database using DATABASE_URL.
"""

import unittest

from sqlalchemy import delete

from backend.catalog import CatalogService, normalize_product
from backend.config import get_settings
from backend.db import get_engine, get_session_factory
from backend.models import ProductSnapshot


IDS = (9900001, 9900002, 9900003, 9900004)
POWER_PATH = "https://ekt.kz/catalog/nizkovoltnaya_apparatura/silovye_avtomaticheskie_vyklyuchateli/drx125_mt_10_250_a_legrand/"


def raw_product(product_id, article, name, price, quantity, current, capacity, url):
    return {
        "id": product_id, "article": article, "name": name,
        "price": price, "quantity": quantity, "url": url,
        "properties": {
            "NOMINALNYY_TOK": current,
            "NOMINALNAYA_OTKLYUCHAYUSHCHAYA_SPOSOBNOST": capacity,
        },
    }


class CatalogSearchTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.catalog = CatalogService(get_session_factory(), None, get_settings())
        rows = [
            raw_product(IDS[0], "X_1", "TestBreaker Alpha", 100, 3, "16 А", "10кА", POWER_PATH + "alpha/"),
            raw_product(IDS[1], "X11", "TestBreaker Beta", 20, 0, "16 А", "10кА", POWER_PATH + "beta/"),
            raw_product(IDS[2], "Y_3", "TestBreaker Gamma", 50, 8, "25 А", "20кА", POWER_PATH + "gamma/"),
            raw_product(IDS[3], "B_4", "TestBox", 15, 2, "16 А", "10кА", "https://ekt.kz/catalog/rozetki_vyklyuchateli_korobki/korobki/raspredelitelnye_1/box/"),
        ]
        async with get_session_factory()() as session:
            for raw in rows:
                item = normalize_product(raw)
                await session.merge(ProductSnapshot(
                    id=item["id"], article=item["article"],
                    supplier_article=item["supplier_article"],
                    normalized=item, raw=raw,
                ))
            await session.commit()

    async def asyncTearDown(self):
        async with get_session_factory()() as session:
            await session.execute(delete(ProductSnapshot).where(ProductSnapshot.id.in_(IDS)))
            await session.commit()
        await get_engine().dispose()

    async def test_exact_article_and_combined_facets_sort_and_page(self):
        exact = await self.catalog.search_catalog(query="X_1")
        self.assertEqual([item["id"] for item in exact["items"]], [IDS[0]])
        self.assertEqual(exact["total"], 1)

        page = await self.catalog.search_catalog(
            query="TestBreaker",
            filters={
                "category": "Силовые автоматические выключатели",
                "in_stock": True,
                "series": ["DRX125 MT"],
                "current": ["16 А", "25 А"],
                "breaking_capacity": ["10кА", "20кА"],
                "sort": "price_asc",
            },
            limit=1, offset=1,
        )
        self.assertEqual(page["total"], 2)
        self.assertEqual([item["id"] for item in page["items"]], [IDS[0]])
        self.assertEqual(page["items"][0]["series"], "DRX125 MT")
        self.assertIsNone(page["items"][0]["unit"])


if __name__ == "__main__":
    unittest.main()
