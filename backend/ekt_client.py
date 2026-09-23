"""Bounded, server-only access to the EKT product API."""

import asyncio
from typing import Any

import httpx


class CatalogUnavailable(Exception):
    pass


class CatalogAccessDenied(CatalogUnavailable):
    """Authentication is absent or EKT rejected it; background jobs must stop."""


class CatalogNotFound(Exception):
    pass


class EktClient:
    def __init__(self, client: httpx.AsyncClient, settings: Any):
        self.client = client
        self.base_url = settings.ekt_api_base_url.rstrip("/")
        self.username = settings.ekt_api_username
        self.password = settings.ekt_api_password
        self.timeout_seconds = float(getattr(settings, "ekt_api_timeout_seconds", 2.5))
        self.semaphore = asyncio.Semaphore(6)

    async def _get(
        self, path: str, params: dict[str, Any], *, timeout_seconds: float | None = None,
    ) -> Any:
        if not self.username or not self.password:
            raise CatalogAccessDenied("Не настроен доступ к каталогу ЕКТ")
        timeout = self.timeout_seconds if timeout_seconds is None else float(timeout_seconds)
        try:
            # The deadline includes waiting for the shared six-request semaphore.
            async with asyncio.timeout(timeout):
                async with self.semaphore:
                    response = await self.client.get(
                        f"{self.base_url}{path}",
                        params=params,
                        auth=httpx.BasicAuth(self.username, self.password),
                        timeout=timeout,
                    )
            if response.status_code == 404:
                raise CatalogNotFound("Товар не найден в каталоге ЕКТ")
            if response.status_code in (401, 403):
                raise CatalogAccessDenied("Доступ к каталогу ЕКТ отклонён")
            response.raise_for_status()
            return response.json()
        except (CatalogNotFound, CatalogAccessDenied):
            raise
        except (httpx.HTTPError, ValueError, TimeoutError) as exc:
            raise CatalogUnavailable("Каталог ЕКТ временно недоступен") from exc

    async def detail(
        self, product_id: int, *, timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        data = await self._get("/products/detail", {"id": product_id}, timeout_seconds=timeout_seconds)
        if not isinstance(data, dict) or not data.get("id"):
            raise CatalogNotFound("Товар не найден в каталоге ЕКТ")
        return data

    async def page(
        self, number: int, per_page: int = 100, *, timeout_seconds: float | None = None,
    ) -> list[dict[str, Any]]:
        data = await self._get(
            "/products", {"page": number, "per_page": per_page}, timeout_seconds=timeout_seconds,
        )
        if isinstance(data, dict):
            data = data.get("items", data.get("products", data.get("data")))
        if not isinstance(data, list):
            raise CatalogUnavailable("Неожиданный ответ каталога ЕКТ")
        return data
