"""Session-owned favorites backed by local product snapshots."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from .db import get_db
from .dependencies import require_mutation_session, require_session
from .errors import APIError
from .models import Favorite, ProductSnapshot, Session


router = APIRouter(prefix="/api/favorites", tags=["favorites"])


@router.get("")
async def list_favorites(
    limit: int = Query(20, ge=1, le=20),
    offset: int = Query(0, ge=0),
    session: Session = Depends(require_session),
    db: AsyncSession = Depends(get_db),
) -> dict:
    total = await db.scalar(select(func.count()).select_from(Favorite).where(Favorite.session_id == session.id))
    rows = (await db.execute(
        select(ProductSnapshot.normalized)
        .join(Favorite, Favorite.product_id == ProductSnapshot.id)
        .where(Favorite.session_id == session.id)
        .order_by(Favorite.created_at.desc(), Favorite.product_id.desc())
        .offset(offset).limit(limit)
    )).scalars().all()
    return {"items": rows, "total": total or 0}


async def _local_product(db: AsyncSession, product_id: int) -> ProductSnapshot:
    product = await db.get(ProductSnapshot, product_id)
    if product is None:
        raise APIError(404, "PRODUCT_NOT_FOUND", "Товар не найден в загруженном каталоге")
    return product


@router.put("/{product_id}")
async def add_favorite(
    product_id: int,
    session: Session = Depends(require_mutation_session),
    db: AsyncSession = Depends(get_db),
) -> dict:
    product = await _local_product(db, product_id)
    statement = pg_insert(Favorite).values(session_id=session.id, product_id=product_id)
    await db.execute(statement.on_conflict_do_nothing(index_elements=[Favorite.session_id, Favorite.product_id]))
    await db.commit()
    return {"favorite": True, "product": product.normalized}


@router.delete("/{product_id}")
async def remove_favorite(
    product_id: int,
    session: Session = Depends(require_mutation_session),
    db: AsyncSession = Depends(get_db),
) -> dict:
    await _local_product(db, product_id)
    await db.execute(delete(Favorite).where(Favorite.session_id == session.id, Favorite.product_id == product_id))
    await db.commit()
    return {"favorite": False, "product_id": product_id}
