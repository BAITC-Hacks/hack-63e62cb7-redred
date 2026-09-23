"""Focused PostgreSQL checks for verified product facets and document filters."""

import unittest

from sqlalchemy import delete

from backend.catalog import CatalogService, normalize_product
from backend.config import get_settings
from backend.db import get_engine, get_session_factory
from backend.errors import APIError
from backend.models import ProductSnapshot


IDS = (9920001, 9920002, 9920003, 9920004)
PRODUCT_URL = "https://ekt.kz/catalog/nizkovoltnaya_apparatura/silovye_avtomaticheskie_vyklyuchateli/drx125_mt_10_250_a_legrand/test/"


def _product(identifier: int, price: int, brand: str | None, documents):
    props = {"NOMINALNYY_TOK": "16 А", "NOMINALNAYA_OTKLYUCHAYUSHCHAYA_SPOSOBNOST": "10 кА"}
    if brand is not None:
        props["TORGOVAYA_MARKA"] = brand
    raw = {
        "id": identifier, "article": f"FACET_{identifier}", "name": f"FacetTest {identifier}",
        "price": price, "quantity": 3, "url": PRODUCT_URL, "properties": props,
    }
    normalized = normalize_product(raw)
    if documents == "missing":
        normalized.pop("documents")
    else:
        normalized["documents"] = documents
    return ProductSnapshot(
        id=identifier, article=normalized["article"],
        supplier_article=normalized["supplier_article"], normalized=normalized, raw=raw,
    )


class CatalogFiltersTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.catalog = CatalogService(get_session_factory(), None, get_settings())
        verified = lambda kind: {"type": kind, "title": kind, "url": f"https://ekt.kz/upload/{kind}.pdf", "source_url": PRODUCT_URL}
        rows = [
            _product(IDS[0], 100, " IEK ", [verified("certificate"), verified("manual")]),
            _product(IDS[1], 150, "Legrand", [verified("refusal_letter")]),
            _product(IDS[2], 50, "GENERICA", "missing"),
            _product(IDS[3], 30, None, None),
        ]
        async with get_session_factory()() as db:
            await db.execute(delete(ProductSnapshot).where(ProductSnapshot.id.in_(IDS)))
            db.add_all(rows)
            await db.commit()

    async def asyncTearDown(self):
        async with get_session_factory()() as db:
            await db.execute(delete(ProductSnapshot).where(ProductSnapshot.id.in_(IDS)))
            await db.commit()
        await get_engine().dispose()

    async def test_documents_certificate_brand_price_and_facets(self):
        async def search(**filters):
            return await self.catalog.search_catalog(query="FacetTest", filters=filters)

        known = await search(has_documents=True)
        self.assertEqual(known["total"], 2)
        self.assertEqual({item["id"] for item in known["items"]}, set(IDS[:2]))
        unknown = await search(has_documents=False)
        self.assertEqual({item["id"] for item in unknown["items"]}, set(IDS[2:]))
        certificates = await search(has_certificates=True)
        self.assertEqual([item["id"] for item in certificates["items"]], [IDS[0]])
        not_certificates = await search(has_certificates=False)
        self.assertEqual({item["id"] for item in not_certificates["items"]}, set(IDS[1:]))
        refusal = await search(document_type=["refusal_letter"])
        self.assertEqual([item["id"] for item in refusal["items"]], [IDS[1]])
        combined = await search(brand=["iek"], has_documents=True, has_certificates=True,
                                min_price="100.00", max_price="100.00")
        self.assertEqual(combined["total"], 1)
        self.assertEqual([item["id"] for item in combined["items"]], [IDS[0]])
        self.assertEqual(combined["items"][0]["brand"], "IEK")
        page = await self.catalog.search_catalog(query="FacetTest", filters={
            "min_price": "50", "max_price": "150", "sort": "price_asc",
        }, limit=1, offset=1)
        self.assertEqual(page["total"], 3)
        self.assertEqual([item["id"] for item in page["items"]], [IDS[0]])

        facets = await self.catalog.get_filter_facets()
        self.assertIn("IEK", facets["brands"])
        self.assertIn("10 кА", facets["breaking_capacity"])
        self.assertIn("certificate", facets["document_types"])
        self.assertIn("refusal_letter", facets["document_types"])
        self.assertNotIn("payment", facets["document_types"])

    async def test_invalid_price_bounds_are_rejected(self):
        for invalid in ("NaN", "Infinity", "-1", "1.001", "10000000000000000"):
            with self.subTest(invalid=invalid), self.assertRaises(APIError) as captured:
                await self.catalog.search_catalog(query="FacetTest", filters={"min_price": invalid})
            self.assertEqual(captured.exception.code, "INVALID_PRICE_RANGE")
        with self.assertRaises(APIError) as captured:
            await self.catalog.search_catalog(query="FacetTest", filters={"min_price": "101", "max_price": "100"})
        self.assertEqual(captured.exception.code, "INVALID_PRICE_RANGE")
