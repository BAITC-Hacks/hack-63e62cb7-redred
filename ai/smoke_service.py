"""Live smoke check of the assistant with local catalog fixtures."""

import asyncio
import json
from pathlib import Path

from ai.evaluate_hypotheses import load_local_env
from backend.assistant.service import reply
from backend.assistant_contract import AssistantContext
from backend.catalog import normalize_product


class Tools:
    def __init__(self):
        raw = json.loads(Path("backend/data/demo_products.json").read_text(encoding="utf-8"))
        self.products = {item["id"]: normalize_product(item) for item in raw}

    async def search_catalog(self, query="", article=None, filters=None, limit=20):
        value = (article or query).casefold()
        items = [p for p in self.products.values() if value in
                 (p["article"].casefold(), p["supplier_article"].casefold()) or value in p["name"].casefold()]
        return {"items": items[:limit], "total": len(items), "catalog_scope": "demo_subset"}

    async def get_product(self, product_id):
        return self.products.get(product_id)

    async def find_analogs(self, product_id, required_quantity=None):
        candidate = self.products[21449]
        return {"source_product_id": product_id, "items": [{"product": candidate,
                "matching_characteristics": [c for c in candidate["characteristics"]
                 if c["code"] in {"KOLICHESTVO_POLYUSOV", "NOMINALNYY_TOK"}]}]} if product_id == 19457 else {"items": []}

    async def get_purchase_terms(self, topic=None):
        return json.loads(Path("backend/data/purchase_terms.json").read_text(encoding="utf-8"))

    async def get_cart(self):
        return {"items": []}


async def main():
    load_local_env()
    cases = [("product", "Проверь наличие и сертификат 010500006_"),
             ("analog", "Найди аналог товара 010500139_, которого нет в наличии"),
             ("proposal", "Добавь 2 штуки товара 010500006_ в корзину")]
    for name, message in cases:
        emitted = []

        async def emit(event):
            emitted.append(event)

        result = await reply(AssistantContext(text=message, language="ru"), Tools(), emit)
        deltas = [e["text"] for e in emitted if e["type"] == "text.delta"]
        assert result.text == "".join(deltas)
        assert len(deltas) > 1, "model response was not streamed"
        assert bool(result.proposed_items) == (name == "proposal"), name
        print(name, result.product_ids, len(result.text), "text_deltas", len(deltas))


if __name__ == "__main__":
    asyncio.run(main())
