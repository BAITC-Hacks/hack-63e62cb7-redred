"""Read-only catalog/cart tools supplied to the assistant for one message."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from typing import Any


class AssistantTools:
    def __init__(self, catalog: Any, cart: dict[str, Any]):
        self._catalog = catalog
        self._cart = cart
        self._product_tasks: dict[tuple[int, bool], asyncio.Task] = {}
        self._lookups = 0

    async def search_catalog(
        self, query: str = "", article: str | None = None,
        filters: dict | None = None, limit: int = 20,
    ) -> dict:
        return await self._catalog.search_catalog(
            query=query, article=article, filters=filters, limit=min(max(limit, 1), 20), offset=0,
        )

    async def get_product(self, product_id: int) -> dict | None:
        return await self.product(product_id)

    async def product(self, product_id: int, *, fresh: bool = True) -> dict | None:
        if type(product_id) is not int or product_id <= 0:
            raise ValueError("invalid product_id")
        key = (product_id, fresh)
        task = self._product_tasks.get(key)
        if task is None:
            if self._lookups >= 3:
                raise ValueError("at most three product details per message")
            self._lookups += 1
            task = asyncio.create_task(self._catalog.get_product(product_id, fresh=fresh))
            self._product_tasks[key] = task
        return deepcopy(await task)

    async def find_analogs(self, product_id: int, required_quantity: str | None = None) -> Any:
        return await self._catalog.find_analogs(
            product_id, required_quantity=required_quantity, limit=5,
        )

    async def get_purchase_terms(self, topic: str | None = None) -> Any:
        return await self._catalog.get_purchase_terms(topic=topic)

    async def get_cart(self) -> dict:
        return self._cart


class CachedProposalCatalog:
    """Let proposal validation reuse details fetched fresh for this message."""

    def __init__(self, tools: AssistantTools):
        self._tools = tools

    async def get_product(self, product_id: int, *, fresh: bool = True) -> dict | None:
        return await self._tools.product(product_id, fresh=True)
