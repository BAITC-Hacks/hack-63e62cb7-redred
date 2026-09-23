"""Bounded reading of already authorized attachments; no extraction to disk."""

import base64
from io import BytesIO
from pathlib import Path
import os
import zipfile
from xml.etree import ElementTree as ET

from backend.errors import APIError


MAX_BYTES = 10 * 1024 * 1024
MAX_TEXT = 24000


def _xml(archive, name):
    data = archive.read(name)
    if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
        raise ValueError("DTD is not allowed")
    return ET.fromstring(data)


def office_text(data: bytes, suffix: str) -> str:
    with zipfile.ZipFile(BytesIO(data)) as archive:
        info = archive.infolist()
        if (len(info) > 500 or sum(i.file_size for i in info) > 50 * 1024 * 1024
                or any(i.file_size > 20 * 1024 * 1024 or i.flag_bits & 1 for i in info)):
            raise ValueError("archive exceeds limits")
        if suffix == ".docx":
            root = _xml(archive, "word/document.xml")
            ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
            value = "\n".join(" ".join(t.text or "" for t in p.iter(ns + "t")) for p in root.iter(ns + "p"))
        else:
            ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
            shared = []
            if "xl/sharedStrings.xml" in archive.namelist():
                shared = ["".join(t.text or "" for t in item.iter(ns + "t"))
                          for item in _xml(archive, "xl/sharedStrings.xml").iter(ns + "si")]
            lines = []
            size = 0
            sheets = sorted(n for n in archive.namelist() if n.startswith("xl/worksheets/sheet") and n.endswith(".xml"))
            for name in sheets:
                for row in _xml(archive, name).iter(ns + "row"):
                    cells = []
                    for cell in row.iter(ns + "c"):
                        raw = cell.findtext(ns + "v", "")
                        if cell.get("t") == "s":
                            raw = shared[int(raw)]
                        elif cell.get("t") == "inlineStr":
                            raw = "".join(t.text or "" for t in cell.iter(ns + "t"))
                        if cell.find(ns + "f") is not None:
                            raw += " [cached formula value; not recalculated]"
                        cells.append(f"{cell.get('r', '')}: {raw}")
                    line = " | ".join(cells)
                    lines.append(line)
                    size += len(line) + 1
                    if size > MAX_TEXT:
                        return "\n".join(lines)[:MAX_TEXT] + "\n[TRUNCATED: ask for a smaller specification]"
            value = "\n".join(lines)
        if len(value) > MAX_TEXT:
            return value[:MAX_TEXT] + "\n[TRUNCATED: ask for a smaller specification]"
        return value


def attachment_content(attachments) -> list[dict]:
    root = Path(os.getenv("UPLOAD_DIR", "backend/uploads")).resolve()
    content = []
    for item in attachments:
        path = Path(item.storage_path).resolve()
        try:
            if path.parent != root:
                raise ValueError("outside upload directory")
            with path.open("rb") as stream:
                data = stream.read(MAX_BYTES + 1)
            if len(data) > MAX_BYTES:
                raise ValueError("oversized attachment")
            suffix = Path(item.filename).suffix.lower()
            if suffix in {".docx", ".xlsx"}:
                value = office_text(data, suffix)
                content.append({"type": "input_text", "text": f"Untrusted attachment {item.filename}:\n{value}"})
            elif suffix in {".jpg", ".jpeg"} and data.startswith(b"\xff\xd8\xff"):
                content.append({"type": "input_image", "image_url": "data:image/jpeg;base64," + base64.b64encode(data).decode("ascii")})
            elif suffix == ".pdf" and data.startswith(b"%PDF-"):
                content.append({"type": "input_file", "filename": item.filename,
                                "file_data": "data:application/pdf;base64," + base64.b64encode(data).decode("ascii")})
            else:
                raise ValueError("unsupported attachment")
        except (OSError, ValueError, KeyError, IndexError, RuntimeError, zipfile.BadZipFile, ET.ParseError):
            raise APIError(422, "ATTACHMENT_UNREADABLE", "Не удалось прочитать вложение. Используйте корректный PDF, JPEG, DOCX или XLSX.") from None
    return content
