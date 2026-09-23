"""Session-bound HTTP and WebSocket chat transport."""

from __future__ import annotations

import asyncio
from collections import defaultdict, deque
import importlib
import re
import time
from typing import Any, Awaitable, Callable, Literal
from types import SimpleNamespace
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field, ValidationError, field_validator
from sqlalchemy import and_, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.assistant_contract import AssistantContext, AssistantResult, AttachmentContext, HistoryMessage
from backend.assistant_tools import AssistantTools, CachedProposalCatalog
from backend.attachments import owned_attachments
from backend.cart import cancel_proposal, confirm_proposal, create_proposal, get_cart_payload, get_pending_proposal
from backend.db import get_db, get_session_factory
from backend.dependencies import require_mutation_session, require_session
from backend.ekt_client import CatalogNotFound, CatalogUnavailable
from backend.errors import APIError
from backend.models import Message, Session
from backend.sessions import COOKIE_NAME, resolve_session_from_cookie, validate_origin
from backend.chat_language import action, language_request, select_language, message as local_message
from backend.chat_memory import search_history


router = APIRouter(prefix="/api/chat", tags=["chat"])
Publish = Callable[[str, dict[str, Any]], Awaitable[None]]
_active: dict[UUID, tuple[UUID, str]] = {}
_rates: dict[UUID, deque[float]] = defaultdict(deque)
_global_active = 0


class MessageInput(BaseModel):
    request_id: UUID
    text: str = Field(default="", max_length=8000)
    attachment_ids: list[UUID] = Field(default_factory=list, max_length=3)
    language: Literal["ru", "kk", "en"] | None = None

    @field_validator("attachment_ids")
    @classmethod
    def unique_attachments(cls, value: list[UUID]) -> list[UUID]:
        if len(set(value)) != len(value):
            raise ValueError("duplicate attachment ID")
        return value


def _fingerprint(body: MessageInput) -> str:
    return repr((body.text, tuple(str(item) for item in body.attachment_ids), body.language))


def _reserve(session_id: UUID, request_id: UUID, fingerprint: str) -> bool:
    global _global_active
    active = _active.get(session_id)
    if active:
        if active == (request_id, fingerprint):
            return False
        if active[0] == request_id:
            raise APIError(409, "REQUEST_ID_CONFLICT", "Этот request_id уже использован с другим содержимым")
        raise APIError(409, "REQUEST_IN_PROGRESS", "Предыдущее сообщение ещё обрабатывается")
    if _global_active >= 3:
        raise APIError(503, "ASSISTANT_BUSY", "Сервис занят, повторите позже")
    now = time.monotonic()
    rate = _rates[session_id]
    while rate and rate[0] <= now - 60:
        rate.popleft()
    if len(rate) >= 10:
        raise APIError(429, "RATE_LIMITED", "Не более десяти сообщений в минуту")
    rate.append(now)
    _active[session_id] = (request_id, fingerprint)
    _global_active += 1
    return True


def _release(session_id: UUID) -> None:
    global _global_active
    if _active.pop(session_id, None) is not None:
        _global_active -= 1


async def _history(db: AsyncSession, session_id: UUID, current_id: UUID) -> list[HistoryMessage]:
    rows = (await db.scalars(
        select(Message).where(
            Message.session_id == session_id, Message.id != current_id,
            Message.status == "completed", Message.role.in_(["user", "assistant"]),
        ).order_by(Message.created_at.desc(), Message.id.desc()).limit(12)
    )).all()
    budget = 12000
    selected: list[HistoryMessage] = []
    for row in rows:
        if budget <= 0:
            break
        value = row.text[-budget:]
        selected.append(HistoryMessage(role=row.role, text=value))
        budget -= len(value)
    return list(reversed(selected))


async def _stored_request(db: AsyncSession, session_id: UUID, body: MessageInput) -> dict | None:
    user = await db.scalar(select(Message).where(
        Message.session_id == session_id, Message.request_id == body.request_id, Message.role == "user",
    ))
    if user is None:
        return None
    if (user.text != body.text or user.attachment_ids != [str(item) for item in body.attachment_ids]
            or user.language != body.language):
        raise APIError(409, "REQUEST_ID_CONFLICT", "Этот request_id уже использован с другим содержимым")
    if user.status == "completed":
        assistant = await db.scalar(select(Message).where(
            Message.session_id == session_id, Message.request_id == body.request_id, Message.role == "assistant",
        ))
        if assistant and assistant.result:
            return assistant.result
        raise APIError(409, "REQUEST_FAILED", "Результат сообщения недоступен; отправьте новый request_id")
    if user.status == "processing":
        # A row left processing after process restart is not replayed or generated again.
        user.status = "failed"
        await db.commit()
    raise APIError(409, "REQUEST_FAILED", "Сообщение не завершилось; отправьте новый request_id")


async def handle_message(
    db: AsyncSession, session: Session, body: MessageInput, catalog: Any, publish: Publish,
) -> dict:
    session_id = session.id
    selected_language = select_language(body.text, body.language, session.preferred_language)
    active = _active.get(session_id)
    if active:
        if active == (body.request_id, _fingerprint(body)):
            await publish("processing.status", {"stage": "generating"})
            return {"status": "processing"}
        if active[0] == body.request_id:
            raise APIError(409, "REQUEST_ID_CONFLICT", "Этот request_id уже использован с другим содержимым")
        raise APIError(409, "REQUEST_IN_PROGRESS", "Предыдущее сообщение ещё обрабатывается")
    existing = await _stored_request(db, session_id, body)
    if existing is not None:
        await publish("assistant.completed", existing)
        return existing
    fingerprint = _fingerprint(body)
    if not _reserve(session_id, body.request_id, fingerprint):
        await publish("processing.status", {"stage": "generating"})
        return {"status": "processing"}
    user_message: Message | None = None
    user_id: UUID | None = None
    proposal_id: str | None = None
    confirming_id: str | None = None
    deltas: list[str] = []
    try:
        rows = await owned_attachments(db, session, body.attachment_ids)
        if not body.text.strip() and not rows:
            raise APIError(422, "EMPTY_MESSAGE", "Введите текст или приложите файл")
        user_id = uuid4()
        user_message = Message(
            id=user_id, session_id=session_id, request_id=body.request_id,
            role="user", text=body.text, language=body.language,
            attachment_ids=[str(item) for item in body.attachment_ids], status="processing",
        )
        db.add(user_message)
        requested_language = body.language or language_request(body.text)
        if requested_language:
            session.preferred_language = requested_language
        await db.commit()
        await publish("message.accepted", {"message_id": str(user_id)})

        async def work() -> dict:
            consent = action(body.text)
            if consent and not rows:
                pending = await get_pending_proposal(db, session)
                if pending is None:
                    phrase = local_message("no_proposal", selected_language)
                    await publish("assistant.delta", {"text": phrase})
                    return await _save_result(db, session_id, user_id, body.request_id, {
                        "language": selected_language, "text": phrase,
                        "products": [], "proposal": None, "cart": None, "cart_url": None, "warnings": [],
                    })
                if consent == "cancel":
                    await cancel_proposal(db, session, pending["id"])
                    phrase = local_message("cancelled", selected_language)
                    await publish("assistant.delta", {"text": phrase})
                    return await _save_result(db, session_id, user_id, body.request_id, {
                        "language": selected_language, "text": phrase, "products": [], "proposal": None,
                        "cart": None, "cart_url": None, "warnings": [],
                    })
                nonlocal confirming_id
                confirming_id = str(pending["id"])
                confirmation = await confirm_proposal(db, session, UUID(confirming_id), catalog)
                phrase = local_message("added", selected_language)
                await publish("assistant.delta", {"text": phrase})
                return await _save_result(db, session_id, user_id, body.request_id, {
                    "language": selected_language, "text": phrase,
                    "products": [], "proposal": confirmation.get("proposal"),
                    "cart": confirmation["cart"], "cart_url": confirmation.get("cart_url", "/cart"), "warnings": [],
                })

            try:
                service = importlib.import_module("backend.assistant.service")
            except ImportError as exc:
                raise APIError(503, "ASSISTANT_UNAVAILABLE", "Ассистент пока недоступен") from exc
            if not hasattr(service, "reply"):
                raise APIError(503, "ASSISTANT_UNAVAILABLE", "Ассистент пока недоступен")
            await publish("processing.status", {"stage": "extracting_attachment" if rows else "generating"})
            cart = await get_cart_payload(db, session)
            pending = await get_pending_proposal(db, session)
            context = AssistantContext(
                text=body.text, language=selected_language,
                history=await _history(db, session_id, user_id),
                attachments=[AttachmentContext(
                    id=str(row.id), filename=row.filename, media_type=row.media_type,
                    storage_path=row.storage_path,
                ) for row in rows],
                cart=cart, pending_proposal=pending,
                conversation_state=session.state or {},
            )
            async def recall(query: str) -> dict:
                return await search_history(get_session_factory(), session_id, query)
            tools = AssistantTools(catalog, cart, history_search=recall)
            total_chars = 0

            async def emit(event: dict[str, str]) -> None:
                nonlocal total_chars
                if not isinstance(event, dict):
                    raise ValueError("invalid assistant event")
                if event.get("type") == "text.delta":
                    fragment = event.get("text")
                    if not isinstance(fragment, str) or total_chars + len(fragment) > 16000 or len(deltas) >= 1024:
                        raise ValueError("invalid text delta")
                    deltas.append(fragment)
                    total_chars += len(fragment)
                    await publish("assistant.delta", {"text": fragment})
                elif event.get("type") == "status":
                    stage = event.get("stage")
                    if stage not in {"searching_catalog", "extracting_attachment", "generating"}:
                        raise ValueError("invalid status stage")
                    await publish("processing.status", {"stage": stage})
                else:
                    raise ValueError("unsupported assistant event")

            try:
                result = AssistantResult.model_validate(await service.reply(context, tools, emit))
            except ValidationError as exc:
                raise APIError(502, "ASSISTANT_INVALID_RESULT", "Ассистент вернул некорректный результат") from exc
            except CatalogNotFound as exc:
                raise APIError(404, "PRODUCT_NOT_FOUND", "Товар не найден") from exc
            except CatalogUnavailable as exc:
                raise APIError(502, "CATALOG_UNAVAILABLE", "Каталог сейчас недоступен") from exc
            except APIError:
                raise
            except Exception as exc:
                raise APIError(503, "ASSISTANT_UNAVAILABLE", "Ассистент сейчас недоступен") from exc
            if result.text != "".join(deltas):
                raise APIError(502, "ASSISTANT_INVALID_RESULT", "Текст ответа не совпал с потоком")
            if any(item.product_id not in result.product_ids for item in result.proposed_items):
                raise APIError(502, "ASSISTANT_INVALID_RESULT", "Предложенный товар отсутствует в карточках")
            products = await asyncio.gather(*(tools.product(product_id) for product_id in result.product_ids))
            if any(product is None for product in products):
                raise APIError(502, "ASSISTANT_INVALID_RESULT", "Ассистент сослался на неизвестный товар")
            if products:
                await publish("products.ready", {"products": products})
            nonlocal proposal_id
            proposal = None
            if result.proposed_items:
                proposal = await create_proposal(
                    db, session, [item.model_dump() for item in result.proposed_items], CachedProposalCatalog(tools),
                )
                proposal_id = proposal["id"]
            return await _save_result(db, session_id, user_id, body.request_id, {
                "language": result.language, "text": result.text, "products": products,
                "proposal": proposal, "cart": None, "cart_url": None, "warnings": result.warnings,
            })

        result = await asyncio.wait_for(work(), timeout=20 if rows else 8)
        await publish("assistant.completed", result)
        return result
    except asyncio.TimeoutError as exc:
        await db.rollback()
        if user_id is not None:
            await db.execute(update(Message).where(Message.id == user_id).values(status="failed"))
            await db.commit()
        raise APIError(504, "REQUEST_TIMEOUT", "Время обработки сообщения истекло") from exc
    except APIError as exc:
        await db.rollback()
        recovery = {"INSUFFICIENT_STOCK": "stock", "INVALID_QUANTITY": "quantity", "PRICE_CHANGED": "price"}
        if user_id is not None and exc.code in recovery:
            if confirming_id:
                await cancel_proposal(db, SimpleNamespace(id=session_id), confirming_id)
            owner = await db.get(Session, session_id, populate_existing=True)
            owner.state = {**(owner.state or {}), "last_cart_error": {"code": exc.code, **exc.details}}
            if exc.details.get("product_id"):
                owner.state = {**owner.state, "last_product_ids": [exc.details["product_id"]]}
            phrase = local_message(recovery[exc.code], selected_language, exc.details)
            prefix = "\n\n" if deltas else ""
            await publish("assistant.delta", {"text": prefix + phrase})
            result = await _save_result(db, session_id, user_id, body.request_id, {
                "language": selected_language, "text": "".join(deltas) + prefix + phrase,
                "products": [], "proposal": None, "cart": await get_cart_payload(db, session_id),
                "cart_url": None, "warnings": [exc.code],
            })
            await publish("assistant.completed", result)
            return result
        if user_id is not None:
            await db.execute(update(Message).where(Message.id == user_id).values(status="failed"))
            await db.commit()
        raise
    except Exception:
        await db.rollback()
        if proposal_id is not None:
            try:
                await cancel_proposal(db, SimpleNamespace(id=session_id), proposal_id)
            except Exception:
                await db.rollback()
        if user_id is not None:
            await db.execute(update(Message).where(Message.id == user_id).values(status="failed"))
            await db.commit()
        raise
    finally:
        _release(session_id)


async def _save_result(db: AsyncSession, session_id: UUID, user_id: UUID, request_id: UUID, payload: dict) -> dict:
    data = jsonable_encoder({"message_id": str(uuid4()), **payload})
    assistant = Message(
        id=UUID(data["message_id"]), session_id=session_id, request_id=request_id,
        role="assistant", text=data["text"], language=data["language"],
        attachment_ids=[], status="completed", result=data,
    )
    db.add(assistant)
    owner = await db.get(Session, session_id)
    if owner is not None:
        state = dict(owner.state or {})
        if payload.get("products"):
            state["last_product_ids"] = [p["id"] for p in payload["products"][:3]]
        if payload.get("proposal"):
            state["last_requested_items"] = [
                {"product_id": p["product_id"], "quantity": p["quantity"]}
                for p in payload["proposal"]["items"][:3]
            ]
            state.pop("last_cart_error", None)
        owner.state = state
    await db.execute(update(Message).where(Message.id == user_id).values(status="completed"))
    await db.commit()
    return data


@router.post("/messages")
async def post_message(
    body: MessageInput, request: Request,
    session: Session = Depends(require_mutation_session),
    db: AsyncSession = Depends(get_db),
) -> dict:
    async def no_stream(_type: str, _data: dict) -> None:
        return None
    return await handle_message(db, session, body, request.app.state.catalog, no_stream)


@router.get("/messages")
async def list_messages(
    session: Session = Depends(require_session), db: AsyncSession = Depends(get_db),
    limit: int = Query(default=50, ge=1, le=50), before_id: UUID | None = None,
) -> dict:
    criteria = [Message.session_id == session.id]
    if before_id is not None:
        before = await db.scalar(select(Message).where(Message.session_id == session.id, Message.id == before_id))
        if before is None:
            raise APIError(404, "MESSAGE_NOT_FOUND", "Сообщение не найдено")
        criteria.append(or_(
            Message.created_at < before.created_at,
            and_(Message.created_at == before.created_at, Message.id < before.id),
        ))
    rows = (await db.scalars(select(Message).where(*criteria).order_by(
        Message.created_at.desc(), Message.id.desc(),
    ).limit(limit + 1))).all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    items = [{
        "id": str(row.id), "request_id": str(row.request_id), "role": row.role,
        "text": row.text, "language": row.language, "attachment_ids": row.attachment_ids,
        "status": row.status, "created_at": row.created_at.isoformat(),
        "result": row.result if row.role == "assistant" else None,
    } for row in reversed(rows)]
    return {"items": items, "next_before_id": str(rows[-1].id) if has_more else None}


@router.websocket("/ws")
async def websocket_chat(websocket: WebSocket) -> None:
    try:
        validate_origin(websocket.headers.get("origin"))
    except APIError:
        await websocket.close(code=4403)
        return
    async with get_session_factory()() as db:
        session = await resolve_session_from_cookie(db, websocket.cookies.get(COOKIE_NAME))
        if session is None:
            await websocket.close(code=4401)
            return
        await websocket.accept()
        while True:
            try:
                incoming = await websocket.receive_json()
            except WebSocketDisconnect:
                return
            request_id = incoming.get("request_id", "") if isinstance(incoming, dict) else ""
            seq = 0
            connected = True

            async def publish(event_type: str, data: dict) -> None:
                nonlocal seq, connected
                seq += 1
                if connected:
                    try:
                        await websocket.send_json(jsonable_encoder({
                            "type": event_type, "request_id": request_id, "seq": seq, "data": data,
                        }))
                    except (WebSocketDisconnect, RuntimeError, OSError):
                        connected = False

            try:
                if not isinstance(incoming, dict) or incoming.get("type") != "message.send":
                    raise APIError(422, "INVALID_MESSAGE", "Ожидается событие message.send")
                body = MessageInput.model_validate({key: value for key, value in incoming.items() if key != "type"})
                request_id = str(body.request_id)
                await handle_message(db, session, body, websocket.app.state.catalog, publish)
            except ValidationError:
                await publish("error", {"code": "INVALID_MESSAGE", "message": "Некорректное сообщение", "retryable": False})
            except APIError as exc:
                await publish("error", {"code": exc.code, "message": exc.message, "retryable": exc.status_code >= 500 or exc.status_code == 429})
            except Exception:
                await publish("error", {"code": "INTERNAL_ERROR", "message": "Не удалось обработать сообщение", "retryable": True})
            if not connected:
                return
