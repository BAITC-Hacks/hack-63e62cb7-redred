"""Private, session-owned attachment storage and bounded format validation."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from uuid import UUID, uuid4
import zipfile

from fastapi import APIRouter, Depends, File, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db import get_db
from backend.config import get_settings
from backend.dependencies import require_mutation_session, require_session
from backend.errors import APIError
from backend.models import Attachment, Session


router = APIRouter(prefix="/api/attachments", tags=["attachments"])
MAX_BYTES = 10 * 1024 * 1024
UPLOAD_ROOT = Path(get_settings().upload_dir).resolve()
MEDIA_TYPES = {
    ".pdf": "application/pdf",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


def _verify_format(filename: str, data: bytes) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix not in MEDIA_TYPES:
        raise APIError(415, "UNSUPPORTED_FILE_TYPE", "Поддерживаются PDF, JPEG, DOCX и XLSX")
    valid = False
    if suffix == ".pdf":
        valid = data.startswith(b"%PDF-") and b"%%EOF" in data[-1024:]
    elif suffix in {".jpg", ".jpeg"}:
        valid = data.startswith(b"\xff\xd8\xff") and data.rstrip().endswith(b"\xff\xd9")
    else:
        try:
            with zipfile.ZipFile(BytesIO(data)) as archive:
                info = archive.infolist()
                names = {entry.filename for entry in info}
                if len(info) > 500 or sum(entry.file_size for entry in info) > 50 * 1024 * 1024:
                    raise ValueError("archive expands beyond limit")
                if any(entry.file_size > 20 * 1024 * 1024 or entry.flag_bits & 1 for entry in info):
                    raise ValueError("oversized or encrypted entry")
                required = {"[Content_Types].xml", "xl/workbook.xml"} if suffix == ".xlsx" else {"[Content_Types].xml", "word/document.xml"}
                valid = required.issubset(names)
                if valid:
                    valid = archive.testzip() is None
        except (ValueError, zipfile.BadZipFile, RuntimeError, OSError):
            valid = False
    if not valid:
        raise APIError(415, "UNSUPPORTED_FILE_TYPE", "Файл не соответствует своему формату")
    return MEDIA_TYPES[suffix]


async def owned_attachments(db: AsyncSession, session: Session, ids: list[UUID]) -> list[Attachment]:
    if len(ids) > 3 or len(set(ids)) != len(ids):
        raise APIError(422, "INVALID_ATTACHMENTS", "Допустимо до трёх разных вложений")
    if not ids:
        return []
    now = datetime.now(timezone.utc)
    rows = (await db.scalars(select(Attachment).where(
        Attachment.id.in_(ids), Attachment.session_id == session.id, Attachment.expires_at > now,
    ))).all()
    if len(rows) != len(ids):
        raise APIError(404, "ATTACHMENT_NOT_FOUND", "Вложение не найдено")
    by_id = {row.id: row for row in rows}
    return [by_id[item] for item in ids]


@router.post("")
async def upload_attachment(
    file: UploadFile = File(...),
    session: Session = Depends(require_mutation_session),
    db: AsyncSession = Depends(get_db),
) -> dict:
    filename = Path(file.filename or "").name[:255]
    if not filename or filename in {".", ".."}:
        raise APIError(422, "INVALID_FILENAME", "Нужно имя файла")
    chunks: list[bytes] = []
    size = 0
    while chunk := await file.read(1024 * 1024):
        size += len(chunk)
        if size > MAX_BYTES:
            raise APIError(413, "FILE_TOO_LARGE", "Размер файла превышает 10 МБ")
        chunks.append(chunk)
    data = b"".join(chunks)
    media_type = await asyncio.to_thread(_verify_format, filename, data)
    identifier = uuid4()
    await asyncio.to_thread(UPLOAD_ROOT.mkdir, parents=True, exist_ok=True)
    destination = UPLOAD_ROOT / str(identifier)
    await asyncio.to_thread(destination.write_bytes, data)
    row = Attachment(
        id=identifier, session_id=session.id, storage_path=str(destination), filename=filename,
        media_type=media_type, size=size, expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    try:
        db.add(row)
        await db.commit()
    except Exception:
        await db.rollback()
        await asyncio.to_thread(destination.unlink, missing_ok=True)
        raise
    return {"attachment_id": str(identifier), "filename": filename, "media_type": media_type, "size": size}


@router.get("/{attachment_id}")
async def download_attachment(
    attachment_id: UUID,
    session: Session = Depends(require_session),
    db: AsyncSession = Depends(get_db),
) -> FileResponse:
    rows = await owned_attachments(db, session, [attachment_id])
    row = rows[0]
    path = Path(row.storage_path).resolve()
    if path.parent != UPLOAD_ROOT or not path.is_file():
        raise APIError(404, "ATTACHMENT_NOT_FOUND", "Вложение не найдено")
    return FileResponse(path, media_type=row.media_type, filename=row.filename)


async def cleanup_expired(db: AsyncSession) -> int:
    """Delete expired files and metadata; suitable for a simple scheduled command."""
    rows = (await db.scalars(select(Attachment).where(Attachment.expires_at <= datetime.now(timezone.utc)))).all()
    for row in rows:
        path = Path(row.storage_path).resolve()
        if path.parent == UPLOAD_ROOT:
            await asyncio.to_thread(path.unlink, missing_ok=True)
        await db.delete(row)
    await db.commit()
    return len(rows)


async def _run_cleanup() -> None:
    from backend.db import get_session_factory

    async with get_session_factory()() as db:
        count = await cleanup_expired(db)
    print(f"Удалено вложений: {count}")


if __name__ == "__main__":
    import sys

    if sys.argv[1:] != ["cleanup"]:
        raise SystemExit("Использование: python -m backend.attachments cleanup")
    asyncio.run(_run_cleanup())
