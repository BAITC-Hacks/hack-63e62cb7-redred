"""FastAPI entry point for the HackAlem prototype."""

import ssl
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from sqlalchemy import text

from . import attachments, cart, chat, sessions
from .catalog import CatalogService
from .config import get_settings
from .db import get_engine, get_session_factory
from .ekt_client import CatalogNotFound, CatalogUnavailable
from .errors import APIError, api_error_handler


@asynccontextmanager
async def lifespan(app: FastAPI):
    # OS trust store is needed for the current Windows EKT certificate chain.
    async with httpx.AsyncClient(verify=ssl.create_default_context(), timeout=5.0) as http_client:
        app.state.catalog = CatalogService(get_session_factory(), http_client, get_settings())
        yield
    await get_engine().dispose()


app = FastAPI(title="HackAlem API", lifespan=lifespan)
app.add_exception_handler(APIError, api_error_handler)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    return await api_error_handler(request, APIError(422, "INVALID_REQUEST", "Некорректные параметры запроса"))


app.include_router(sessions.router)
app.include_router(cart.router)
app.include_router(attachments.router)
app.include_router(chat.router)


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
async def search_products(request: Request, q: str = "", limit: int = Query(20, ge=1, le=20), offset: int = Query(0, ge=0)) -> dict:
    return await request.app.state.catalog.search_catalog(query=q, limit=limit, offset=offset)


@app.get("/api/products/{product_id}")
async def get_product(product_id: int, request: Request) -> dict:
    return await request.app.state.catalog.get_product(product_id, fresh=True)


@app.get("/api/products/{product_id}/analogs")
async def get_analogs(product_id: int, request: Request, limit: int = Query(5, ge=1, le=5)) -> dict:
    return await request.app.state.catalog.find_analogs(product_id, limit=limit)


@app.get("/api/purchase-conditions")
async def get_purchase_conditions(request: Request) -> dict:
    return await request.app.state.catalog.get_purchase_terms()
