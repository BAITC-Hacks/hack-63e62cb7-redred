"""The narrow, server-owned interface used by backend.assistant.service."""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator


class AttachmentContext(BaseModel):
    id: str
    filename: str
    media_type: str
    storage_path: str  # Server-only. Never put this value in a browser event.


class HistoryMessage(BaseModel):
    role: Literal["user", "assistant"]
    text: str


class AssistantContext(BaseModel):
    text: str
    language: Literal["ru", "kk", "en"] | None = None
    history: list[HistoryMessage] = Field(default_factory=list)
    attachments: list[AttachmentContext] = Field(default_factory=list)
    cart: dict[str, Any] = Field(default_factory=dict)
    pending_proposal: dict[str, Any] | None = None


class ProposedItem(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    product_id: int
    quantity: str

    @field_validator("product_id")
    @classmethod
    def positive_product_id(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("product_id must be positive")
        return value

    @field_validator("quantity")
    @classmethod
    def valid_quantity(cls, value: str) -> str:
        from decimal import Decimal, InvalidOperation

        if len(value) > 32:
            raise ValueError("quantity is too long")
        try:
            number = Decimal(value)
        except InvalidOperation as exc:
            raise ValueError("quantity must be decimal") from exc
        if not number.is_finite() or number <= 0 or number.as_tuple().exponent < -3:
            raise ValueError("quantity must be positive with at most 3 decimals")
        return value


class AssistantResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    language: Literal["ru", "kk", "en"]
    text: str = Field(max_length=16000)
    product_ids: list[int] = Field(default_factory=list, max_length=3)
    proposed_items: list[ProposedItem] = Field(default_factory=list, max_length=20)
    warnings: list[str] = Field(default_factory=list, max_length=10)

    @field_validator("product_ids")
    @classmethod
    def valid_product_ids(cls, value: list[int]) -> list[int]:
        if any(type(item) is not int or item <= 0 for item in value) or len(set(value)) != len(value):
            raise ValueError("product_ids must be distinct positive integers")
        return value


Emit = Callable[[dict[str, str]], Awaitable[None]]


class AssistantService(Protocol):
    async def reply(self, context: AssistantContext, tools: Any, emit: Emit) -> AssistantResult: ...
