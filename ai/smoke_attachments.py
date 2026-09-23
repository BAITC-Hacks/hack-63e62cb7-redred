"""Live attachment check through upload, chat, OpenAI and EKT read API.

Requires the same environment and database setup as ai.smoke_full.
The JPEG is the public EKT product image; the PDF is generated in memory.
"""

import asyncio
import json
import os
import ssl
import tempfile
from pathlib import Path
from uuid import uuid4

import httpx

from ai.evaluate_hypotheses import load_local_env


def sample_pdf() -> bytes:
    content = b"BT /F1 18 Tf 50 750 Td (Article 010500006_) Tj ET"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    result = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, obj in enumerate(objects, start=1):
        offsets.append(len(result))
        result.extend(f"{number} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = len(result)
    result.extend(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        result.extend(f"{offset:010d} 00000 n \n".encode())
    result.extend(f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return bytes(result)


async def main() -> None:
    load_local_env()
    image_url = json.loads(Path("backend/data/demo_products.json").read_text(encoding="utf-8"))[1]["image"]
    async with httpx.AsyncClient(verify=ssl.create_default_context(), timeout=10) as public:
        image_response = await public.get(image_url)
        image_response.raise_for_status()
        image = image_response.content
    assert image.startswith(b"\xff\xd8\xff") and image.rstrip().endswith(b"\xff\xd9")

    with tempfile.TemporaryDirectory(prefix="redred-uploads-") as upload_dir:
        os.environ["UPLOAD_DIR"] = upload_dir
        from backend.main import app, lifespan

        async with lifespan(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://localhost:5173",
                headers={"Origin": "http://localhost:5173"},
            ) as client:
                session = await client.post("/api/session", json={})
                session.raise_for_status()
                client.headers["X-CSRF-Token"] = session.json()["csrf_token"]

                async def check(filename: str, data: bytes, question: str) -> dict:
                    uploaded = await client.post("/api/attachments", files={"file": (filename, data)})
                    uploaded.raise_for_status()
                    response = await client.post("/api/chat/messages", json={
                        "request_id": str(uuid4()), "text": question,
                        "attachment_ids": [uploaded.json()["attachment_id"]], "language": "ru",
                    })
                    response.raise_for_status()
                    result = response.json()
                    assert result["text"].strip()
                    assert result["proposal"] is None
                    return result

                photo = await check("breaker.jpg", image, "Что изображено на фото? Если артикул не читается, скажи об этом.")
                print("JPEG", "products", len(photo["products"]), "text_chars", len(photo["text"]))
                pdf = await check("specification.pdf", sample_pdf(), "Найди товар по артикулу в PDF и проверь наличие.")
                assert [p["id"] for p in pdf["products"]] == [21449], pdf["text"]
                print("PDF", "product_id", pdf["products"][0]["id"], "text_chars", len(pdf["text"]))


if __name__ == "__main__":
    asyncio.run(main())
