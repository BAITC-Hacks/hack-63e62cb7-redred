"""Admin-only AI model discovery, probe and activation routes."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field

from .admin import _mutation, require_admin
from .errors import APIError
from .models import AdminSession


router = APIRouter(prefix="/api/admin/ai", tags=["admin-ai"])


class ModelIn(BaseModel):
    model: str = Field(min_length=1, max_length=256)


class ActivateIn(ModelIn):
    check_id: UUID


def _control(request: Request):
    control = getattr(request.app.state, "ai_models", None)
    if control is None:
        raise APIError(503, "AI_CONTROL_UNAVAILABLE", "Управление AI-моделью сейчас недоступно")
    return control


@router.get("/models")
async def list_models(
    request: Request, response: Response, admin: AdminSession = Depends(require_admin),
) -> dict:
    response.headers["Cache-Control"] = "no-store"
    return await _control(request).list_models()


@router.get("/status")
async def model_status(
    request: Request, response: Response, admin: AdminSession = Depends(require_admin),
) -> dict:
    response.headers["Cache-Control"] = "no-store"
    return await _control(request).get_status()


@router.post("/check")
async def check_model(
    body: ModelIn, request: Request, response: Response,
    admin: AdminSession = Depends(require_admin),
) -> dict:
    _mutation(request, admin)
    response.headers["Cache-Control"] = "no-store"
    return await _control(request).check_model(body.model, admin.id)


@router.post("/activate")
async def activate_model(
    body: ActivateIn, request: Request, response: Response,
    admin: AdminSession = Depends(require_admin),
) -> dict:
    _mutation(request, admin)
    response.headers["Cache-Control"] = "no-store"
    return await _control(request).activate(body.model, body.check_id, admin.id)
