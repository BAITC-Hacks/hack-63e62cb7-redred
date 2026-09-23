"""Client accounts bound to durable chat/cart sessions, separate from admin auth."""

from __future__ import annotations

import asyncio
import base64
from collections import defaultdict, deque
from datetime import timedelta
import hashlib
import hmac
import re
import secrets
import time
import uuid

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .cart import _public_proposal, create_proposal, money_text, qty_text
from .config import get_settings
from .db import get_db, get_engine
from .dependencies import require_mutation_session, require_session
from .errors import APIError
from .models import (
    Attachment, Cart, CartItem, CartMergeDraft, CartProposal, ClientLogin, Favorite,
    Message, Session, User, utcnow,
)
from .sessions import CLIENT_COOKIE_NAME, COOKIE_NAME, resolve_session_from_cookie, token_hash


router = APIRouter(prefix="/api/auth", tags=["auth"])
LOGIN_DAYS = 30
PASSWORD_MIN = 8
_attempts: dict[str, deque[float]] = defaultdict(deque)


class Credentials(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=PASSWORD_MIN, max_length=256)


class Registration(Credentials):
    name: str | None = Field(default=None, max_length=100)


def _email(value: str) -> str:
    normalized = value.strip().casefold()
    if not re.fullmatch(r"[^@\s]{1,64}@[^@\s]{1,255}", normalized):
        raise APIError(422, "INVALID_EMAIL", "Некорректный адрес электронной почты")
    return normalized


def _hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=16384, r=8, p=1, dklen=32)
    return "scrypt$16384$8$1$" + base64.urlsafe_b64encode(salt).decode() + "$" + base64.urlsafe_b64encode(digest).decode()


def _verify_password(password: str, encoded: str | None) -> bool:
    if not encoded:
        hashlib.scrypt(password.encode("utf-8"), salt=b"hackalem-dummy!!", n=16384, r=8, p=1, dklen=32)
        return False
    try:
        scheme, n, r, p, salt_text, digest_text = encoded.split("$")
        if scheme != "scrypt" or (int(n), int(r), int(p)) != (16384, 8, 1):
            return False
        salt = base64.urlsafe_b64decode(salt_text)
        expected = base64.urlsafe_b64decode(digest_text)
        actual = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=int(n), r=int(r), p=int(p), dklen=len(expected))
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def _rate_limit(request: Request, email: str) -> None:
    now = time.monotonic()
    address = request.client.host if request.client else "unknown"
    key = address + "|" + email
    if len(_attempts) > 4096:
        for old in list(_attempts):
            if not _attempts[old] or _attempts[old][-1] < now - 60:
                _attempts.pop(old, None)
    queue = _attempts[key]
    while queue and queue[0] < now - 60:
        queue.popleft()
    if len(queue) >= 5:
        raise APIError(429, "AUTH_RATE_LIMITED", "Слишком много попыток входа; подождите минуту")
    queue.append(now)


def _user_public(user: User) -> dict:
    return {"id": str(user.id), "email": user.email, "name": user.name, "role": user.role}


def _busy(session_id: uuid.UUID) -> None:
    from .chat import is_session_busy

    if is_session_busy(session_id):
        raise APIError(409, "AUTH_BUSY", "Дождитесь завершения текущего сообщения и повторите вход")


def _set_login_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        CLIENT_COOKIE_NAME, token, httponly=True, secure=get_settings().secure_cookie,
        samesite="lax", max_age=LOGIN_DAYS * 86400, path="/",
    )
    response.delete_cookie(COOKIE_NAME, path="/", secure=get_settings().secure_cookie, samesite="lax")


def _new_login(user: User) -> tuple[ClientLogin, str]:
    token = secrets.token_urlsafe(48)
    row = ClientLogin(
        user_id=user.id, session_id=user.session_id, token_hash=token_hash(token),
        csrf_token=secrets.token_urlsafe(32), expires_at=utcnow() + timedelta(days=LOGIN_DAYS),
    )
    return row, token


def _login_payload(user: User, login: ClientLogin, draft: CartMergeDraft | None = None) -> dict:
    return {
        "user": _user_public(user),
        "csrf_token": login.csrf_token,
        "expires_at": login.expires_at.isoformat(),
        "cart_merge_draft": _draft_public(draft, None) if draft else None,
    }


def _draft_public(draft: CartMergeDraft, proposal: CartProposal | None) -> dict:
    status = "confirmed" if proposal and proposal.status == "confirmed" else "pending"
    items = []
    for item in draft.items:
        items.append({
            "product_id": item["product_id"], "name": item["name"],
            "quantity": item["quantity"], "unit": item["unit"],
            "unit_price": item["unit_price"], "price_checked_at": item["price_checked_at"],
        })
    return {
        "id": str(draft.id), "status": status, "items": items,
        "proposal_id": str(draft.proposal_id) if draft.proposal_id else None,
        "created_at": draft.created_at.isoformat(),
    }


async def _merge_guest(db: AsyncSession, guest: Session, account: Session) -> CartMergeDraft | None:
    guest_cart = await db.scalar(select(Cart).where(Cart.session_id == guest.id).with_for_update())
    account_cart = await db.scalar(select(Cart).where(Cart.session_id == account.id).with_for_update())
    if guest_cart is None or account_cart is None:
        raise APIError(500, "CART_MISSING", "Корзина сессии не найдена")
    guest_items = list((await db.scalars(select(CartItem).where(CartItem.cart_id == guest_cart.id))).all())
    account_has_items = await db.scalar(select(CartItem.id).where(CartItem.cart_id == account_cart.id).limit(1)) is not None
    draft = None
    if guest_items and account_has_items:
        draft = CartMergeDraft(
            session_id=account.id,
            items=[{
                "product_id": row.product_id, "name": row.name, "quantity": qty_text(row.quantity),
                "unit": row.unit, "unit_price": money_text(row.unit_price),
                "price_checked_at": row.price_checked_at.isoformat(),
            } for row in guest_items],
        )
        db.add(draft)
    elif guest_items:
        for row in guest_items:
            row.cart_id = account_cart.id
        account_cart.revision += 1

    existing_request_ids = set((await db.scalars(
        select(Message.request_id).where(Message.session_id == account.id)
    )).all())
    guest_messages = list((await db.scalars(
        select(Message).where(Message.session_id == guest.id).order_by(Message.created_at, Message.id)
    )).all())
    remapped: dict[uuid.UUID, uuid.UUID] = {
        request_id: uuid.uuid4()
        for request_id in {row.request_id for row in guest_messages}
        if request_id in existing_request_ids
    }
    for row in guest_messages:
        if row.request_id in remapped:
            row.request_id = remapped[row.request_id]
        row.session_id = account.id

    await db.execute(update(Attachment).where(Attachment.session_id == guest.id).values(session_id=account.id))
    favorites = list((await db.scalars(select(Favorite).where(Favorite.session_id == guest.id))).all())
    account_favorites = set((await db.scalars(
        select(Favorite.product_id).where(Favorite.session_id == account.id)
    )).all())
    for favorite in favorites:
        if favorite.product_id not in account_favorites:
            db.add(Favorite(session_id=account.id, product_id=favorite.product_id, created_at=favorite.created_at))
            account_favorites.add(favorite.product_id)
    if account.preferred_language is None:
        account.preferred_language = guest.preferred_language
    account.state = {**(guest.state or {}), **(account.state or {})}
    await db.flush()
    await db.delete(guest)
    await db.flush()
    return draft


@router.post("/register")
async def register(
    body: Registration, response: Response,
    session: Session = Depends(require_mutation_session), db: AsyncSession = Depends(get_db),
) -> dict:
    if getattr(session, "_auth_user", None) is not None:
        raise APIError(409, "ALREADY_AUTHENTICATED", "Вы уже вошли в аккаунт")
    _busy(session.id)
    email = _email(body.email)
    password_hash = await asyncio.to_thread(_hash_password, body.password)
    guest_id = session.id
    await db.commit()
    try:
        async with db.begin():
            guest = await db.scalar(select(Session).where(Session.id == guest_id).with_for_update())
            if guest is None or await db.scalar(select(User.id).where(User.session_id == guest_id)) is not None:
                raise APIError(409, "SESSION_CHANGED", "Сессия изменилась; обновите страницу")
            if await db.scalar(select(User.id).where(User.email == email)) is not None:
                raise APIError(409, "EMAIL_IN_USE", "Адрес уже зарегистрирован")
            user = User(email=email, password_hash=password_hash, name=body.name.strip() or None if body.name else None,
                        role="client", session_id=guest_id)
            db.add(user)
            await db.flush()
            login, token = _new_login(user)
            db.add(login)
            # The browser's old guest cookie can never authorize this account.
            guest.token_hash = token_hash(secrets.token_urlsafe(48))
            await db.flush()
            payload = _login_payload(user, login)
    except IntegrityError:
        raise APIError(409, "EMAIL_IN_USE", "Адрес уже зарегистрирован") from None
    _set_login_cookie(response, token)
    return payload


@router.post("/login")
async def login(
    body: Credentials, request: Request, response: Response,
    session: Session = Depends(require_mutation_session), db: AsyncSession = Depends(get_db),
) -> dict:
    if getattr(session, "_auth_user", None) is not None:
        raise APIError(409, "ALREADY_AUTHENTICATED", "Вы уже вошли в аккаунт")
    _busy(session.id)
    email = _email(body.email)
    _rate_limit(request, email)
    user = await db.scalar(select(User).where(User.email == email))
    valid = await asyncio.to_thread(_verify_password, body.password, user.password_hash if user else None)
    if user is None or not valid:
        raise APIError(401, "INVALID_CREDENTIALS", "Неверный адрес или пароль")
    guest_id = session.id
    user_id = user.id
    account_id = user.session_id
    _busy(account_id)
    await db.commit()
    async with db.begin():
        user = await db.scalar(select(User).where(User.id == user_id).with_for_update())
        guest = await db.scalar(select(Session).where(Session.id == guest_id).with_for_update())
        account = await db.scalar(select(Session).where(Session.id == account_id).with_for_update())
        if user is None or guest is None or account is None:
            raise APIError(409, "SESSION_CHANGED", "Сессия изменилась; обновите страницу")
        if await db.scalar(select(User.id).where(User.session_id == guest_id)) is not None:
            raise APIError(409, "SESSION_CHANGED", "Гостевая сессия уже привязана")
        draft = await _merge_guest(db, guest, account)
        login_row, token = _new_login(user)
        db.add(login_row)
        await db.flush()
        payload = _login_payload(user, login_row, draft)
    _set_login_cookie(response, token)
    return payload


@router.post("/logout")
async def logout(
    response: Response, session: Session = Depends(require_mutation_session),
    db: AsyncSession = Depends(get_db),
) -> dict:
    login_id = getattr(session, "_client_login_id", None)
    if login_id is None:
        raise APIError(401, "CLIENT_LOGIN_REQUIRED", "Сначала войдите в аккаунт")
    _busy(session.id)
    await db.execute(delete(ClientLogin).where(ClientLogin.id == login_id))
    await db.commit()
    response.delete_cookie(CLIENT_COOKIE_NAME, path="/", secure=get_settings().secure_cookie, samesite="lax")
    return {"status": "logged_out"}


@router.get("/me")
async def me(request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    session = await resolve_session_from_cookie(
        db, request.cookies.get(COOKIE_NAME), request.cookies.get(CLIENT_COOKIE_NAME),
    )
    user = getattr(session, "_auth_user", None) if session else None
    if user is None:
        return {"authenticated": False, "user": None, "csrf_token": None, "expires_at": None}
    return {
        "authenticated": True, "user": _user_public(user),
        "csrf_token": session._request_csrf_token,
        "expires_at": session._request_expires_at.isoformat(),
    }


def _require_client(session: Session) -> User:
    user = getattr(session, "_auth_user", None)
    if user is None:
        raise APIError(401, "CLIENT_LOGIN_REQUIRED", "Сначала войдите в аккаунт")
    return user


@router.get("/cart-drafts")
async def cart_drafts(session: Session = Depends(require_session), db: AsyncSession = Depends(get_db)) -> dict:
    _require_client(session)
    drafts = list((await db.scalars(
        select(CartMergeDraft).where(CartMergeDraft.session_id == session.id)
        .order_by(CartMergeDraft.created_at.desc())
    )).all())
    proposal_ids = [draft.proposal_id for draft in drafts if draft.proposal_id]
    proposals = {}
    if proposal_ids:
        proposals = {row.id: row for row in (await db.scalars(
            select(CartProposal).where(CartProposal.id.in_(proposal_ids))
        )).all()}
    return {"items": [_draft_public(draft, proposals.get(draft.proposal_id)) for draft in drafts]}


@router.post("/cart-drafts/{draft_id}/proposal")
async def prepare_cart_draft(
    draft_id: uuid.UUID, request: Request, session: Session = Depends(require_mutation_session),
    db: AsyncSession = Depends(get_db),
) -> dict:
    _require_client(session)
    # create_proposal commits independently; retain a session-level lock until
    # its ID is linked to the draft, including across API processes.
    lock_key = int.from_bytes(hashlib.sha256(draft_id.bytes + b"cart-draft").digest()[:8], "big", signed=True)
    connection = await get_engine().connect()
    acquired = False
    try:
        acquired = bool(await connection.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": lock_key}))
        await connection.commit()
        if not acquired:
            raise APIError(409, "DRAFT_PREPARING", "Черновик уже обрабатывается; повторите запрос")
        draft = await db.scalar(
            select(CartMergeDraft)
            .where(CartMergeDraft.id == draft_id, CartMergeDraft.session_id == session.id)
        )
        if draft is None:
            raise APIError(404, "CART_DRAFT_NOT_FOUND", "Черновик корзины не найден")
        proposal = await db.get(CartProposal, draft.proposal_id) if draft.proposal_id else None
        if proposal and proposal.status in {"pending", "confirmed"} and (proposal.status == "confirmed" or proposal.expires_at > utcnow()):
            return {"draft": _draft_public(draft, proposal), "proposal": _public_proposal(proposal)}
        items = [{"product_id": item["product_id"], "quantity": item["quantity"]} for item in draft.items]
        created = await create_proposal(db, session, items, request.app.state.catalog)
        draft.proposal_id = uuid.UUID(created["id"])
        await db.commit()
        return {"draft": _draft_public(draft, None), "proposal": created}
    finally:
        if acquired:
            try:
                await connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": lock_key})
                await connection.commit()
            finally:
                await connection.close()
        else:
            await connection.close()
