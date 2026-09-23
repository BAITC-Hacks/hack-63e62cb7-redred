"""FastAPI entry point for the HackAlem prototype."""

import ssl
from contextlib import asynccontextmanager
from typing import Literal

import httpx
from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from sqlalchemy import text

from . import admin, admin_ai, attachments, auth, cart, chat, favorites, sessions
from .ai_control import AIModelControl
from .catalog import CatalogService
from .catalog_sync import CatalogSynchronizer
from .config import get_settings
from .db import get_engine, get_session_factory
from .ekt_client import CatalogNotFound, CatalogUnavailable
from .errors import APIError, api_error_handler


@asynccontextmanager
async def lifespan(app: FastAPI):
    # OS trust store is needed for the current Windows EKT certificate chain.
    async with httpx.AsyncClient(verify=ssl.create_default_context(), timeout=5.0) as http_client:
        settings = get_settings()
        app.state.catalog = CatalogService(get_session_factory(), http_client, settings)
        app.state.ai_models = AIModelControl(get_session_factory(), http_client)
        app.state.catalog.ai_model_control = app.state.ai_models
        app.state.catalog_sync = CatalogSynchronizer(get_engine(), get_session_factory(), app.state.catalog, settings)
        await app.state.catalog_sync.start()
        try:
            yield
        finally:
            await app.state.catalog_sync.stop()
    await get_engine().dispose()


app = FastAPI(title="HackAlem API", lifespan=lifespan)
app.add_exception_handler(APIError, api_error_handler)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    return await api_error_handler(request, APIError(422, "INVALID_REQUEST", "Некорректные параметры запроса"))


app.include_router(sessions.router)
app.include_router(auth.router)
app.include_router(favorites.router)
app.include_router(cart.router)
app.include_router(attachments.router)
app.include_router(chat.router)
app.include_router(admin.router)
app.include_router(admin_ai.router)


@app.exception_handler(CatalogNotFound)
async def catalog_not_found(request: Request, exc: CatalogNotFound):
    return await api_error_handler(request, APIError(404, "PRODUCT_NOT_FOUND", "Товар не найден"))


@app.exception_handler(CatalogUnavailable)
async def catalog_unavailable(request: Request, exc: CatalogUnavailable):
    return await api_error_handler(request, APIError(502, "CATALOG_UNAVAILABLE", "Каталог сейчас недоступен"))


@app.get("/health/")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/ready")
async def ready() -> dict[str, str]:
    try:
        async with get_engine().connect() as connection:
            await connection.execute(text("SELECT 1"))
    except Exception:
        raise APIError(503, "DATABASE_UNAVAILABLE", "База данных недоступна") from None
    return {"status": "ok"}


@app.get("/api/products")
async def search_products(
    request: Request,
    q: str = Query("", max_length=200),
    article: str | None = Query(None, max_length=255),
    category: str | None = None,
    in_stock: bool = False,
    sort: Literal["relevance", "price_asc", "price_desc"] = "relevance",
    series: list[str] | None = Query(None),
    current: list[str] | None = Query(None),
    breaking_capacity: list[str] | None = Query(None),
    limit: int = Query(20, ge=1, le=20),
    offset: int = Query(0, ge=0),
) -> dict:
    return await request.app.state.catalog.search_catalog(
        query=q, article=article,
        filters={
            "category": category, "in_stock": in_stock, "sort": sort,
            "series": series, "current": current,
            "breaking_capacity": breaking_capacity,
        },
        limit=limit, offset=offset,
    )


@app.get("/api/products/{product_id}")
async def get_product(product_id: int, request: Request) -> dict:
    return await request.app.state.catalog.get_product(product_id, fresh=True)


@app.get("/api/products/{product_id}/analogs")
async def get_analogs(product_id: int, request: Request, limit: int = Query(5, ge=1, le=5)) -> dict:
    return await request.app.state.catalog.find_analogs(product_id, limit=limit)


@app.get("/api/purchase-conditions")
async def get_purchase_conditions(request: Request) -> dict:
    return await request.app.state.catalog.get_purchase_terms()
