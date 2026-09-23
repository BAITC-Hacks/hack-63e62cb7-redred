"""Limited, read-only probe of the ekt.kz catalog API.

The probe is intentionally bounded. It verifies response shapes, latency, stock
consistency, recommendation IDs, possible certificate fields, and obvious field
conflicts without crawling the complete catalog.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

from evaluate_hypotheses import load_local_env


DEFAULT_BASE_URL = "https://ekt.kz/api"
MAX_PAGES = 10
MAX_DETAILS = 25
REQUEST_TIMEOUT_SECONDS = 20
REQUEST_ATTEMPTS = 2
CERTIFICATE_KEY_PATTERN = re.compile(
    r"cert|sert|passport|pasport|document|документ|сертификат|паспорт|файл",
    re.IGNORECASE,
)
AMP_PATTERN = re.compile(r"(?<!\d)(\d{1,4})\s*[AaАа](?![A-Za-zА-Яа-я])")


class CatalogClient:
    def __init__(self, base_url: str, username: str, password: str) -> None:
        self.base_url = base_url.rstrip("/")
        token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
        self.authorization = f"Basic {token}"

    def get(self, path: str, params: dict[str, Any] | None = None) -> tuple[Any, int]:
        query = urllib.parse.urlencode(params or {})
        url = f"{self.base_url}/{path.lstrip('/')}"
        if query:
            url += f"?{query}"
        request = urllib.request.Request(
            url,
            headers={"Authorization": self.authorization, "Accept": "application/json"},
            method="GET",
        )
        started = time.perf_counter()
        last_error: Exception | None = None
        for attempt in range(1, REQUEST_ATTEMPTS + 1):
            try:
                with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                    data = json.loads(response.read().decode("utf-8"))
                return data, round((time.perf_counter() - started) * 1000)
            except urllib.error.HTTPError as error:
                body = error.read().decode("utf-8", errors="replace")[:500]
                raise RuntimeError(f"HTTP {error.code} for {path}: {body}") from error
            except (urllib.error.URLError, TimeoutError) as error:
                last_error = error
                if attempt < REQUEST_ATTEMPTS:
                    time.sleep(0.25)
        raise RuntimeError(
            f"Network error for {path} after {REQUEST_ATTEMPTS} attempts: {last_error}"
        ) from last_error


def iter_key_values(value: Any, prefix: str = "") -> Iterable[tuple[str, Any]]:
    if isinstance(value, dict):
        for key, nested in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            yield path, nested
            yield from iter_key_values(nested, path)
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            yield from iter_key_values(nested, f"{prefix}[{index}]")


def possible_certificate_fields(product: dict[str, Any]) -> list[dict[str, str]]:
    matches: list[dict[str, str]] = []
    for path, value in iter_key_values(product):
        if not CERTIFICATE_KEY_PATTERN.search(path):
            continue
        if isinstance(value, (str, int, float, bool)) and str(value).strip():
            matches.append({"path": path, "value": str(value)[:300]})
    return matches[:20]


def amp_values(text: Any) -> list[int]:
    if not isinstance(text, str):
        return []
    return sorted({int(match.group(1)) for match in AMP_PATTERN.finditer(text)})


def property_amp_values(properties: Any) -> list[int]:
    if not isinstance(properties, dict):
        return []
    values: set[int] = set()
    for key, value in properties.items():
        if "TOK" in str(key).upper() or "CURRENT" in str(key).upper():
            values.update(amp_values(value))
    return sorted(values)


def sanitize_product(product: dict[str, Any]) -> dict[str, Any]:
    stores = product.get("stores") if isinstance(product.get("stores"), list) else []
    positive_stores = [
        {
            "id": store.get("id"),
            "name": store.get("name"),
            "quantity": store.get("quantity"),
        }
        for store in stores
        if isinstance(store, dict) and isinstance(store.get("quantity"), (int, float)) and store["quantity"] > 0
    ]
    stock_sum = sum(float(store["quantity"]) for store in positive_stores)
    quantity = product.get("quantity")
    name_amps = amp_values(product.get("name"))
    description_amps = amp_values(product.get("description"))
    properties = product.get("properties") if isinstance(product.get("properties"), dict) else {}
    property_amps = property_amp_values(properties)
    all_nonempty = [set(values) for values in (name_amps, description_amps, property_amps) if values]
    conflict = len({tuple(sorted(values)) for values in all_nonempty}) > 1
    recommend = properties.get("RECOMMEND")
    if not isinstance(recommend, list):
        recommend = []
    return {
        "id": product.get("id"),
        "article": product.get("article"),
        "name": str(product.get("name") or "")[:200],
        "quantity": quantity,
        "stock_sum": stock_sum,
        "stock_matches_store_sum": (
            abs(float(quantity) - stock_sum) < 1e-9
            if isinstance(quantity, (int, float))
            else None
        ),
        "positive_stores": positive_stores,
        "recommend_ids": [str(item) for item in recommend[:20]],
        "possible_certificate_fields": possible_certificate_fields(product),
        "amp_evidence": {
            "name": name_amps,
            "description": description_amps,
            "properties": property_amps,
            "conflict": conflict,
        },
        "url": product.get("url"),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pages", type=int, default=3)
    parser.add_argument("--details", type=int, default=12)
    parser.add_argument("--known-id", type=int, default=515291)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    load_local_env()
    args = parse_args()
    pages = max(1, min(args.pages, MAX_PAGES))
    detail_limit = max(1, min(args.details, MAX_DETAILS))
    username = os.getenv("EKT_API_USERNAME", "").strip()
    password = os.getenv("EKT_API_PASSWORD", "").strip()
    if not username or not password:
        print(
            json.dumps(
                {
                    "status": "skipped",
                    "reason": (
                        "EKT_API_USERNAME/EKT_API_PASSWORD are not set. "
                        "Put them in local .env; never commit them."
                    ),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2

    client = CatalogClient(
        os.getenv("EKT_API_BASE_URL", DEFAULT_BASE_URL),
        username,
        password,
    )
    page_results: list[dict[str, Any]] = []
    item_ids: list[int] = []
    errors: list[str] = []
    for page in range(1, pages + 1):
        try:
            payload, duration = client.get("products", {"page": page})
            items = payload.get("items") if isinstance(payload, dict) else None
            items = items if isinstance(items, list) else []
            page_results.append(
                {
                    "page": page,
                    "duration_ms": duration,
                    "declared_page": payload.get("page") if isinstance(payload, dict) else None,
                    "per_page": payload.get("per_page") if isinstance(payload, dict) else None,
                    "count": payload.get("count") if isinstance(payload, dict) else None,
                    "items": len(items),
                }
            )
            for item in items:
                if isinstance(item, dict) and isinstance(item.get("id"), int):
                    item_ids.append(item["id"])
        except Exception as error:
            errors.append(str(error)[:500])
            break

    ordered_ids: list[int] = []
    for product_id in [args.known_id, *item_ids]:
        if product_id not in ordered_ids:
            ordered_ids.append(product_id)
        if len(ordered_ids) >= detail_limit:
            break

    details: list[dict[str, Any]] = []
    detail_latencies: list[int] = []
    for product_id in ordered_ids:
        try:
            product, duration = client.get("products/detail", {"id": product_id})
            detail_latencies.append(duration)
            if isinstance(product, dict):
                item = sanitize_product(product)
                item["duration_ms"] = duration
                details.append(item)
        except Exception as error:
            errors.append(f"detail {product_id}: {str(error)[:400]}")

    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "passed" if page_results and details and not errors else "partial",
        "limits": {"pages": pages, "details": detail_limit},
        "summary": {
            "list_pages_checked": len(page_results),
            "list_items_seen": sum(item["items"] for item in page_results),
            "details_checked": len(details),
            "zero_stock_found": sum(item.get("quantity") == 0 for item in details),
            "recommendations_found": sum(bool(item.get("recommend_ids")) for item in details),
            "certificate_fields_found": sum(bool(item.get("possible_certificate_fields")) for item in details),
            "amp_conflicts_found": sum(bool(item.get("amp_evidence", {}).get("conflict")) for item in details),
            "average_detail_latency_ms": (
                round(sum(detail_latencies) / len(detail_latencies)) if detail_latencies else None
            ),
        },
        "pages": page_results,
        "products": details,
        "errors": errors,
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
