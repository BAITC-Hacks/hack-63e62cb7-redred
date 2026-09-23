import copy
import json
import unittest
from decimal import Decimal

from backend.catalog import DATA_DIR, normalize_product
from backend.cart import _product_values
from backend.errors import APIError


class SaleMultiplicityTest(unittest.TestCase):
    def setUp(self):
        self.cards = json.loads((DATA_DIR / "demo_products.json").read_text(encoding="utf-8"))
        self.raw = copy.deepcopy(next(item for item in self.cards if item["id"] == 515282))

    def test_reported_article_can_be_purchased(self):
        product = normalize_product(self.raw)
        self.assertEqual((product["unit"], product["quantity_step"]), ("piece", "1"))
        self.assertEqual(_product_values(product, Decimal(1))["product_id"], 515282)

    def test_all_twenty_breakers_and_boxes(self):
        enabled = [normalize_product(raw) for raw in self.cards if raw.get("properties", {}).get("KRATNOST_MIN") == "1"]
        self.assertEqual(len(enabled), 20)
        self.assertTrue(all(item["unit"] == "piece" and item["quantity_step"] == "1" for item in enabled))

    def test_supplier_pack_size_is_enforced(self):
        self.raw["properties"]["KRATNOST_MIN"] = "12"
        product = normalize_product(self.raw)
        with self.assertRaises(APIError) as caught:
            _product_values(product, Decimal(1))
        self.assertEqual(caught.exception.code, "INVALID_QUANTITY")
        _product_values(product, Decimal(12))

    def test_unknown_units_and_invalid_steps_stay_blocked(self):
        for value in (None, "0", "-1", "NaN", "abc", "0.5"):
            with self.subTest(value=value):
                self.raw["properties"]["KRATNOST_MIN"] = value
                self.assertIsNone(normalize_product(self.raw)["unit"])
        self.raw["properties"]["KRATNOST_MIN"] = "1"
        self.raw["url"] = "https://ekt.kz/catalog/kabel_provod/test/"
        self.assertIsNone(normalize_product(self.raw)["unit"])
