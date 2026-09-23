import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import get_settings
from .db import get_db
from .dependencies import require_mutation_session, require_session
from .ekt_client import CatalogNotFound, CatalogUnavailable
from .errors import APIError
from .models import Cart, CartItem, CartProposal, Session, utcnow


router = APIRouter(prefix="/api/cart", tags=["cart"])


class ProposedItemIn(BaseModel):
    product_id: int = Field(gt=0)
    quantity: str


class ProposalIn(BaseModel):
    items: list[ProposedItemIn] = Field(min_length=1, max_length=20)


class ConfirmIn(BaseModel):
    confirmed: bool


def decimal_value(value: object, code: str = "INVALID_QUANTITY") -> Decimal:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise APIError(422, code, "Некорректное число") from None
    if not number.is_finite():
        raise APIError(422, code, "Число должно быть конечным")
    return number


def quantity_value(value: object) -> Decimal:
    number = decimal_value(value)
    if number <= 0 or number.as_tuple().exponent < -3 or number >= Decimal("1000000000000000"):
        raise APIError(422, "INVALID_QUANTITY", "Количество должно быть положительным с точностью до 0.001")
    return number


def qty_text(value: Decimal) -> str:
    return format(value.normalize(), "f")


def money_text(value: Decimal) -> str:
    return format(value.quantize(Decimal("0.01")), ".2f")


def _dt(value: datetime | str | None) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return value or utcnow().isoformat()


def _product_values(product: dict, quantity: Decimal) -> dict:
    if not product or not isinstance(product, dict):
        raise APIError(404, "PRODUCT_NOT_FOUND", "Товар не найден")
    unit = product.get("unit")
    step_raw = product.get("quantity_step")
    if unit is None or step_raw is None:
        raise APIError(422, "PRODUCT_NOT_PURCHASABLE", "Единица или шаг продажи не определены")
    step = decimal_value(step_raw)
    if step <= 0 or quantity % step != 0:
        raise APIError(422, "INVALID_QUANTITY", "Количество не соответствует шагу продажи", {"quantity_step": str(step_raw)})
    price_raw = product.get("price")
    if price_raw is None:
        raise APIError(422, "PRODUCT_NOT_PURCHASABLE", "Цена не определена")
    price = decimal_value(price_raw, "PRODUCT_NOT_PURCHASABLE")
    if price <= 0 or price.as_tuple().exponent < -2:
        raise APIError(422, "PRODUCT_NOT_PURCHASABLE", "Цена недоступна для покупки")
    stock_raw = product.get("available_quantity")
    if stock_raw is None:
        raise APIError(422, "PRODUCT_NOT_PURCHASABLE", "Остаток не определён")
    stock = decimal_value(stock_raw, "PRODUCT_NOT_PURCHASABLE")
    if stock < 0:
        stock = Decimal(0)
    return {
        "product_id": int(product["id"]),
        "name": str(product.get("name") or f"Товар {product['id']}"),
        "unit": str(unit),
        "quantity": quantity,
        "unit_price": price,
        "stock": stock,
        "price_checked_at": _dt(product.get("checked_at")),
    }


def _public_proposal(proposal: CartProposal) -> dict:
    items = []
    total = Decimal(0)
    for raw in proposal.items:
        quantity = quantity_value(raw["quantity"])
        price = decimal_value(raw["unit_price"])
        line_total = quantity * price
        total += line_total
        items.append({
            "product_id": raw["product_id"],
            "name": raw["name"],
            "quantity": qty_text(quantity),
            "unit": raw["unit"],
            "unit_price": money_text(price),
            "line_total": money_text(line_total),
        })
    return {
        "id": str(proposal.id),
        "status": proposal.status,
        "expires_at": proposal.expires_at.isoformat(),
        "items": items,
        "total": money_text(total),
        "currency": get_settings().currency,
    }


async def _cart_row(db: AsyncSession, session_id: uuid.UUID, *, lock: bool = False) -> Cart:
    statement = select(Cart).where(Cart.session_id == session_id)
    if lock:
        statement = statement.with_for_update()
    cart = await db.scalar(statement)
    if cart is None:
        raise APIError(500, "CART_MISSING", "Корзина сессии не найдена")
    return cart


async def _cart_items(db: AsyncSession, cart_id: uuid.UUID) -> list[CartItem]:
    return list((await db.scalars(select(CartItem).where(CartItem.cart_id == cart_id).order_by(CartItem.product_id))).all())


async def get_cart_payload(db: AsyncSession, session: Session | uuid.UUID) -> dict:
    session_id = session if isinstance(session, uuid.UUID) else session.id
    cart = await _cart_row(db, session_id)
    rows = await _cart_items(db, cart.id)
    items = []
    total = Decimal(0)
    for row in rows:
        line_total = row.quantity * row.unit_price
        total += line_total
        items.append({
            "product_id": row.product_id,
            "name": row.name,
            "quantity": qty_text(row.quantity),
            "unit": row.unit,
            "unit_price": money_text(row.unit_price),
            "line_total": money_text(line_total),
            "price_checked_at": row.price_checked_at.isoformat(),
        })
    return {
        "id": str(cart.id),
        "revision": cart.revision,
        "items": items,
        "line_count": len(items),
        "total": money_text(total),
        "currency": get_settings().currency,
        "cart_url": "/cart",
    }


async def get_pending_proposal(db: AsyncSession, session: Session) -> dict | None:
    proposal = await db.scalar(
        select(CartProposal)
        .where(CartProposal.session_id == session.id, CartProposal.status == "pending", CartProposal.expires_at > utcnow())
        .order_by(CartProposal.created_at.desc())
        .limit(1)
    )
    return _public_proposal(proposal) if proposal else None


async def _fetch_products(catalog, ids: list[int]) -> dict[int, dict]:
    async def one(product_id: int) -> tuple[int, dict]:
        try:
            product = await catalog.get_product(product_id, fresh=True)
        except Exception as exc:
            if isinstance(exc, CatalogNotFound):
                raise APIError(404, "PRODUCT_NOT_FOUND", "Товар не найден") from exc
            if isinstance(exc, CatalogUnavailable):
                raise APIError(502, "CATALOG_UNAVAILABLE", "Каталог сейчас недоступен") from exc
            raise
        return product_id, product

    try:
        return dict(await asyncio.wait_for(asyncio.gather(*(one(i) for i in ids)), timeout=8))
    except TimeoutError:
        raise APIError(502, "CATALOG_UNAVAILABLE", "Каталог не ответил вовремя") from None


def _merge_items(items: list[dict]) -> dict[int, Decimal]:
    merged: dict[int, Decimal] = {}
    for item in items:
        product_id = int(item["product_id"])
        if product_id <= 0:
            raise APIError(422, "INVALID_PRODUCT_ID", "Некорректный ID товара")
        merged[product_id] = merged.get(product_id, Decimal(0)) + quantity_value(item["quantity"])
    if not merged or len(merged) > 20:
        raise APIError(422, "INVALID_ITEMS", "Нужно указать от 1 до 20 товаров")
    return merged


def _check_stock(values: dict, existing: Decimal) -> None:
    if existing + values["quantity"] > values["stock"]:
        raise APIError(
            409, "INSUFFICIENT_STOCK", "Недостаточно товара на общем остатке",
            {"product_id": values["product_id"], "available_quantity": qty_text(values["stock"]), "already_in_cart": qty_text(existing)},
        )


async def create_proposal(db: AsyncSession, session: Session, items: list[dict], catalog) -> dict:
    session_id = session.id
    merged = _merge_items(items)
    products = await _fetch_products(catalog, list(merged))
    validated = {pid: _product_values(products[pid], quantity) for pid, quantity in merged.items()}
    await db.commit()
    async with db.begin():
        await db.scalar(select(Session.id).where(Session.id == session_id).with_for_update())
        cart = await _cart_row(db, session_id, lock=True)
        existing = {row.product_id: row.quantity for row in await _cart_items(db, cart.id)}
        for values in validated.values():
            _check_stock(values, existing.get(values["product_id"], Decimal(0)))
        pending = list((await db.scalars(select(CartProposal).where(CartProposal.session_id == session_id, CartProposal.status == "pending").with_for_update())).all())
        for old in pending:
            old.status = "replaced"
        proposal = CartProposal(
            session_id=session_id,
            cart_id=cart.id,
            base_revision=cart.revision,
            items=[{
                "product_id": v["product_id"], "name": v["name"], "unit": v["unit"],
                "quantity": qty_text(v["quantity"]), "unit_price": money_text(v["unit_price"]),
                "price_checked_at": v["price_checked_at"],
            } for v in validated.values()],
            expires_at=utcnow() + timedelta(minutes=10),
            status="pending",
        )
        db.add(proposal)
        await db.flush()
        payload = _public_proposal(proposal)
    return payload


async def confirm_proposal(db: AsyncSession, session: Session, proposal_id: uuid.UUID | str, catalog) -> dict:
    session_id = session.id
    try:
        proposal_uuid = uuid.UUID(str(proposal_id))
    except ValueError:
        raise APIError(404, "PROPOSAL_NOT_FOUND", "Предложение не найдено") from None
    preflight = await db.scalar(select(CartProposal).where(CartProposal.id == proposal_uuid, CartProposal.session_id == session_id))
    if preflight is None:
        raise APIError(404, "PROPOSAL_NOT_FOUND", "Предложение не найдено")
    if preflight.status == "confirmed":
        return preflight.confirmed_result
    if preflight.status == "replaced":
        raise APIError(409, "PROPOSAL_REPLACED", "Предложение заменено")
    if preflight.status != "pending" or preflight.expires_at <= utcnow():
        raise APIError(409, "PROPOSAL_EXPIRED", "Предложение больше не действует")
    raw_items = preflight.items
    products = await _fetch_products(catalog, [int(raw["product_id"]) for raw in raw_items])
    validated = {}
    for raw in raw_items:
        pid = int(raw["product_id"])
        values = _product_values(products[pid], quantity_value(raw["quantity"]))
        if values["unit_price"] != decimal_value(raw["unit_price"]):
            raise APIError(409, "PRICE_CHANGED", "Цена товара изменилась", {"product_id": pid, "current_price": money_text(values["unit_price"])})
        validated[pid] = values
    await db.commit()
    async with db.begin():
        await db.scalar(select(Session.id).where(Session.id == session_id).with_for_update())
        cart = await _cart_row(db, session_id, lock=True)
        proposal = await db.scalar(
            select(CartProposal)
            .where(CartProposal.id == proposal_uuid, CartProposal.session_id == session_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if proposal is None:
            raise APIError(404, "PROPOSAL_NOT_FOUND", "Предложение не найдено")
        if proposal.status == "confirmed":
            return proposal.confirmed_result
        if proposal.status == "replaced":
            raise APIError(409, "PROPOSAL_REPLACED", "Предложение заменено")
        if proposal.status != "pending" or proposal.expires_at <= utcnow():
            raise APIError(409, "PROPOSAL_EXPIRED", "Предложение больше не действует")
        if proposal.base_revision != cart.revision:
            raise APIError(409, "CART_CHANGED", "Корзина изменилась; нужно новое предложение")
        existing = {row.product_id: row for row in await _cart_items(db, cart.id)}
        for values in validated.values():
            row = existing.get(values["product_id"])
            if row and row.unit_price != values["unit_price"]:
                raise APIError(409, "PRICE_CHANGED", "Цена товара в корзине отличается от текущей", {"product_id": values["product_id"]})
            _check_stock(values, row.quantity if row else Decimal(0))
        for values in validated.values():
            row = existing.get(values["product_id"])
            checked_at = datetime.fromisoformat(values["price_checked_at"].replace("Z", "+00:00"))
            if row:
                row.quantity += values["quantity"]
                row.unit_price = values["unit_price"]
                row.name = values["name"]
                row.unit = values["unit"]
                row.price_checked_at = checked_at
            else:
                db.add(CartItem(
                    cart_id=cart.id, product_id=values["product_id"], quantity=values["quantity"],
                    unit_price=values["unit_price"], name=values["name"], unit=values["unit"],
                    price_checked_at=checked_at,
                ))
        cart.revision += 1
        proposal.status = "confirmed"
        await db.flush()
        cart_payload = await get_cart_payload(db, session_id)
        result = {"proposal": _public_proposal(proposal), "cart": cart_payload, "cart_url": "/cart"}
        proposal.confirmed_result = result
    return result


async def cancel_proposal(db: AsyncSession, session: Session, proposal_id: uuid.UUID | str) -> dict:
    session_id = session.id
    try:
        proposal_uuid = uuid.UUID(str(proposal_id))
    except ValueError:
        raise APIError(404, "PROPOSAL_NOT_FOUND", "Предложение не найдено") from None
    await db.commit()
    async with db.begin():
        await db.scalar(select(Session.id).where(Session.id == session_id).with_for_update())
        cart = await _cart_row(db, session_id, lock=True)
        proposal = await db.scalar(
            select(CartProposal)
            .where(CartProposal.id == proposal_uuid, CartProposal.session_id == session_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if proposal is None:
            raise APIError(404, "PROPOSAL_NOT_FOUND", "Предложение не найдено")
        if proposal.status == "pending":
            proposal.status = "cancelled"
        elif proposal.status == "confirmed":
            raise APIError(409, "PROPOSAL_CONFIRMED", "Предложение уже подтверждено")
    return {"status": "cancelled"}


@router.get("")
async def get_cart(request: Request, session: Session = Depends(require_session), db: AsyncSession = Depends(get_db)) -> dict:
    return await get_cart_payload(db, session)


@router.post("/proposals")
async def post_proposal(body: ProposalIn, request: Request, session: Session = Depends(require_mutation_session), db: AsyncSession = Depends(get_db)) -> dict:
    return await create_proposal(db, session, [item.model_dump() for item in body.items], request.app.state.catalog)


@router.post("/proposals/{proposal_id}/confirm")
async def post_confirm(proposal_id: uuid.UUID, body: ConfirmIn, request: Request, session: Session = Depends(require_mutation_session), db: AsyncSession = Depends(get_db)) -> dict:
    if body.confirmed is not True:
        raise APIError(422, "CONFIRMATION_REQUIRED", "Для добавления требуется confirmed: true")
    return await confirm_proposal(db, session, proposal_id, request.app.state.catalog)


@router.post("/proposals/{proposal_id}/cancel")
async def post_cancel(proposal_id: uuid.UUID, request: Request, session: Session = Depends(require_mutation_session), db: AsyncSession = Depends(get_db)) -> dict:
    return await cancel_proposal(db, session, proposal_id)
