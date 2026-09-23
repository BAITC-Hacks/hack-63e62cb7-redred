"""Local searchable EKT snapshots and conservative product normalization."""

import json
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import Numeric, and_, case, cast, func, literal, or_, select, true
from sqlalchemy.dialects.postgresql import JSONB, insert as pg_insert

from .ekt_client import CatalogNotFound, CatalogUnavailable, EktClient
from .errors import APIError
from .models import ProductSnapshot


DATA_DIR = Path(__file__).with_name("data")
DEMO_IDS = {19457, 21449, 515280, 515291}
DEMO_DOCUMENTS = json.loads((DATA_DIR / "demo_documents.json").read_text(encoding="utf-8"))
PRODUCT_FIELDS = {
    "KOLICHESTVO_POLYUSOV": "Количество полюсов",
    "NOMINALNYY_TOK": "Номинальный ток",
    "NOMINALNAYA_OTKLYUCHAYUSHCHAYA_SPOSOBNOST": "Отключающая способность",
    "NOMINALNOE_NAPRYAZHENIE": "Номинальное напряжение",
    "KHARAKTERISTIKA_SRABATYVANIYA": "Характеристика срабатывания",
    "TORGOVAYA_MARKA": "Торговая марка",
}
ANALOG_KEYS = {
    "Модульные автоматические выключатели": (
        "KOLICHESTVO_POLYUSOV", "NOMINALNYY_TOK",
        "NOMINALNAYA_OTKLYUCHAYUSHCHAYA_SPOSOBNOST",
        "NOMINALNOE_NAPRYAZHENIE", "KHARAKTERISTIKA_SRABATYVANIYA",
    ),
    "Силовые автоматические выключатели": (
        "KOLICHESTVO_POLYUSOV", "NOMINALNYY_TOK",
        "NOMINALNAYA_OTKLYUCHAYUSHCHAYA_SPOSOBNOST",
        "NOMINALNOE_NAPRYAZHENIE",
    ),
}


def _decimal(value: Any) -> Decimal | None:
    try:
        number = Decimal(str(value).replace(",", "."))
        return number if number.is_finite() else None
    except (InvalidOperation, TypeError, ValueError):
        return None


def _source_url(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    parsed = urlparse(value)
    return value if parsed.scheme == "https" and parsed.hostname == "ekt.kz" else None


def _category(url: str | None) -> str:
    if url and "/modulnye_avtomaticheskie_vyklyuchateli/" in url:
        return "Модульные автоматические выключатели"
    if url and "/silovye_avtomaticheskie_vyklyuchateli/" in url:
        return "Силовые автоматические выключатели"
    if url and "/rozetki_vyklyuchateli_korobki/korobki/" in url:
        return "Коробки"
    if url and "/spets_predlozhenie/" in url:
        return "Спецпредложения"
    return "Каталог ЕКТ"


def _series(url: str | None) -> str | None:
    if url and "/drx125_mt_10_250_a_legrand/" in url:
        return "DRX125 MT"
    if url and "/drx250_mt_10_250_a_legrand/" in url:
        return "DRX250 MT"
    return None


def _like_literal(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _filter_values(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    return [str(item).strip() for item in value if str(item).strip()]


def _json_array(value: Any) -> Any:
    """Treat absent, null or malformed JSONB arrays as empty."""
    return case(
        (func.jsonb_typeof(value) == "array", value),
        else_=literal([], type_=JSONB),
    )


def _price_bound(value: Any) -> Decimal:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise APIError(422, "INVALID_PRICE_RANGE", "Некорректный предел цены") from None
    if (not amount.is_finite() or amount < 0 or amount.as_tuple().exponent < -2
            or amount > Decimal("9999999999999999.99")):
        raise APIError(422, "INVALID_PRICE_RANGE", "Цена должна быть неотрицательной с точностью до 0.01")
    return amount


def _comp(value: Any) -> str:
    """Compare formatting variants such as '16 А' and '16А'."""
    return re.sub(r"\s+", "", str(value or "").casefold()).replace(",", ".")


def normalize_product(raw: dict[str, Any], checked_at: str | None = None) -> dict[str, Any]:
    props = raw.get("properties") if isinstance(raw.get("properties"), dict) else {}
    warnings: list[str] = []
    price = _decimal(raw.get("price"))
    if price is None or price <= 0:
        price_string = None
        warnings.append("Цена отсутствует или не является положительной; покупка недоступна")
    else:
        price_string = str(price.quantize(Decimal("0.01")))

    source_qty = _decimal(raw.get("quantity"))
    quantity = max(0, int(source_qty.to_integral_value(rounding=ROUND_DOWN))) if source_qty is not None else 0
    if source_qty is None:
        warnings.append("Общий остаток отсутствует")
    stores = []
    for store in raw.get("stores") or []:
        if not isinstance(store, dict):
            continue
        amount = _decimal(store.get("quantity"))
        if amount is not None:
            stores.append({"id": store.get("id"), "name": store.get("name"), "quantity": str(amount)})
    if stores and source_qty is not None:
        store_sum = sum(Decimal(item["quantity"]) for item in stores)
        if store_sum != source_qty:
            warnings.append("Общий остаток и сумма остатков по складам различаются; для покупки используется общий остаток")

    product_id = int(raw["id"])
    if product_id == 515291:
        warnings.append("В названии указан ток 160 А, в свойстве NOMINALNYY_TOK — 250 А; требуется уточнение")
    if product_id == 515279:
        warnings.append("В названии указан ток 40 А, в свойстве NOMINALNYY_TOK — 125 А; требуется уточнение")
    unit, step = ("piece", "1") if product_id in DEMO_IDS else (None, None)
    if unit is None:
        warnings.append("Единица и шаг продажи не проверены; покупка недоступна")
    product_url = _source_url(raw.get("url"))
    brand = str(props.get("TORGOVAYA_MARKA") or "").strip() or None
    return {
        "id": product_id,
        "article": str(raw.get("article") or props.get("CML2_ARTICLE") or ""),
        "supplier_article": str(props.get("ARTIKULPOSTAVSHCHIKA") or ""),
        "name": str(raw.get("name") or ""),
        "category": _category(product_url),
        "series": _series(product_url),
        "brand": brand,
        "price": price_string,
        "currency": "KZT",
        "available_quantity": str(quantity),
        "stock_scope": "all_warehouses",
        "unit": unit,
        "quantity_step": step,
        "stores": stores,
        "description": str(raw.get("description") or ""),
        "characteristics": [
            {"code": key, "name": label, "value": str(props[key])}
            for key, label in PRODUCT_FIELDS.items() if props.get(key) is not None
        ],
        "documents": DEMO_DOCUMENTS.get(str(product_id), []) if product_url == raw.get("url") else [],
        "product_url": product_url,
        "image_url": _source_url(raw.get("image")),
        "data_warnings": warnings,
        "checked_at": checked_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }


class CatalogService:
    def __init__(self, db_session_factory: Any, http_client: Any, settings: Any):
        self.session_factory = db_session_factory
        self.ekt = EktClient(http_client, settings)

    async def save_raw(
        self, raw: dict[str, Any], checked_at: str | datetime | None = None,
    ) -> dict[str, Any]:
        """Keep the newest observation, ordered by request start time in UTC.

        A slow background GET may finish after a newer foreground GET. Its old
        observation must not overwrite the newer snapshot. `updated_at` carries
        this observation time; `checked_at` exposes the same time in the card.
        """
        if checked_at is None:
            observed_at = datetime.now(timezone.utc)
        elif isinstance(checked_at, datetime):
            observed_at = checked_at
        else:
            observed_at = datetime.fromisoformat(checked_at.replace("Z", "+00:00"))
        if observed_at.tzinfo is None:
            raise ValueError("checked_at must include a timezone")
        observed_at = observed_at.astimezone(timezone.utc)
        checked_at_utc = observed_at.isoformat().replace("+00:00", "Z")
        product = normalize_product(raw, checked_at_utc)
        statement = pg_insert(ProductSnapshot).values(
            id=product["id"], article=product["article"],
            supplier_article=product["supplier_article"],
            normalized=product, raw=raw, updated_at=observed_at,
        )
        statement = statement.on_conflict_do_update(
            index_elements=[ProductSnapshot.id],
            set_={
                "article": statement.excluded.article,
                "supplier_article": statement.excluded.supplier_article,
                "normalized": statement.excluded.normalized,
                "raw": statement.excluded.raw,
                "updated_at": statement.excluded.updated_at,
            },
            where=ProductSnapshot.updated_at < statement.excluded.updated_at,
        ).returning(ProductSnapshot.normalized)
        async with self.session_factory() as session:
            stored = (await session.execute(statement)).scalar_one_or_none()
            if stored is None:
                stored = (await session.execute(
                    select(ProductSnapshot.normalized).where(ProductSnapshot.id == product["id"])
                )).scalar_one()
            await session.commit()
        return stored

    async def get_product(self, product_id: int, fresh: bool = False) -> dict[str, Any]:
        if not fresh:
            async with self.session_factory() as session:
                snapshot = await session.get(ProductSnapshot, int(product_id))
                if snapshot is not None:
                    return snapshot.normalized
        # Every fresh call reaches EKT. Failure cannot fall back to a stale snapshot.
        observed_at = datetime.now(timezone.utc)
        raw = await self.ekt.detail(int(product_id))
        if int(raw["id"]) != int(product_id):
            raise CatalogUnavailable("ЕКТ вернул карточку другого товара")
        return await self.save_raw(raw, checked_at=observed_at)

    async def search_catalog(
        self, query: str = "", article: str | None = None,
        filters: dict[str, Any] | None = None, limit: int = 20, offset: int = 0,
    ) -> dict[str, Any]:
        limit = max(1, min(int(limit), 20))
        offset = max(0, int(offset))
        filters = filters or {}
        item = ProductSnapshot.normalized
        name = item["name"].astext
        price = cast(item["price"].astext, Numeric(18, 2))
        quantity = cast(item["available_quantity"].astext, Numeric(18, 3))
        documents = _json_array(item["documents"])
        conditions = []
        if filters.get("category"):
            conditions.append(item["category"].astext == str(filters["category"]))
        if filters.get("available_only") or filters.get("in_stock"):
            conditions.append(quantity > 0)
        series = _filter_values(filters.get("series"))
        if series:
            conditions.append(func.lower(item["series"].astext).in_([v.casefold() for v in series]))
        brands = _filter_values(filters.get("brand"))
        if brands:
            characteristics = func.jsonb_array_elements(_json_array(item["characteristics"])).table_valued("value").lateral("brand_characteristic")
            code = func.jsonb_extract_path_text(characteristics.c.value, "code")
            value = func.lower(func.btrim(func.jsonb_extract_path_text(characteristics.c.value, "value")))
            conditions.append(select(1).select_from(characteristics).where(
                code == "TORGOVAYA_MARKA", value.in_([brand.casefold() for brand in brands]),
            ).correlate(ProductSnapshot).exists())
        if filters.get("has_documents") is not None:
            count = func.jsonb_array_length(documents)
            conditions.append(count > 0 if filters["has_documents"] else count == 0)
        if filters.get("has_certificates") is not None:
            certificate = documents.contains([{"type": "certificate"}])
            conditions.append(certificate if filters["has_certificates"] else ~certificate)
        document_types = _filter_values(filters.get("document_type"))
        if document_types:
            conditions.append(or_(*(
                documents.contains([{"type": document_type}]) for document_type in document_types
            )))
        minimum = filters.get("min_price")
        maximum = filters.get("max_price")
        minimum = _price_bound(minimum) if minimum is not None else None
        maximum = _price_bound(maximum) if maximum is not None else None
        if minimum is not None and maximum is not None and minimum > maximum:
            raise APIError(422, "INVALID_PRICE_RANGE", "Минимальная цена больше максимальной")
        if minimum is not None:
            conditions.append(price >= minimum)
        if maximum is not None:
            conditions.append(price <= maximum)
        for filter_name, code in (
            ("current", "NOMINALNYY_TOK"),
            ("breaking_capacity", "NOMINALNAYA_OTKLYUCHAYUSHCHAYA_SPOSOBNOST"),
        ):
            values = _filter_values(filters.get(filter_name))
            if values:
                conditions.append(or_(*(
                    item["characteristics"].contains([{"code": code, "value": value}])
                    for value in values
                )))
        sought_article = str(article or "").strip().casefold()
        if sought_article:
            conditions.append(or_(
                func.lower(ProductSnapshot.article) == sought_article,
                func.lower(ProductSnapshot.supplier_article) == sought_article,
            ))
        words = str(query or "").casefold().split()
        exact = None
        if words:
            query_text = " ".join(words)
            exact = or_(
                func.lower(ProductSnapshot.article) == query_text,
                func.lower(ProductSnapshot.supplier_article) == query_text,
            )
            word_matches = and_(*(
                name.ilike(f"%{_like_literal(word)}%", escape="\\") for word in words
            ))
            conditions.append(or_(exact, word_matches))
        sort = filters.get("sort", "relevance")
        if sort == "price_asc":
            ordering = (price.asc().nulls_last(), func.lower(name), ProductSnapshot.id)
        elif sort == "price_desc":
            ordering = (price.desc().nulls_last(), func.lower(name), ProductSnapshot.id)
        else:
            ordering = (
                *((case((exact, 0), else_=1),) if exact is not None else ()),
                func.lower(name), ProductSnapshot.id,
            )
        async with self.session_factory() as session:
            total = (await session.execute(
                select(func.count()).select_from(ProductSnapshot).where(*conditions)
            )).scalar_one()
            rows = (await session.execute(
                select(ProductSnapshot.normalized).where(*conditions)
                .order_by(*ordering).offset(offset).limit(limit)
            )).scalars().all()
        return {"items": rows, "total": total, "catalog_scope": "demo_subset"}

    async def get_filter_facets(self) -> dict[str, Any]:
        """Return only filter values present in loaded product snapshots."""
        item = ProductSnapshot.normalized
        characteristic = func.jsonb_array_elements(_json_array(item["characteristics"])).table_valued("value").lateral("facet_characteristic")
        characteristic_code = func.jsonb_extract_path_text(characteristic.c.value, "code")
        characteristic_value = func.btrim(func.jsonb_extract_path_text(characteristic.c.value, "value"))
        document = func.jsonb_array_elements(_json_array(item["documents"])).table_valued("value").lateral("facet_document")
        document_type = func.btrim(func.jsonb_extract_path_text(document.c.value, "type"))
        async with self.session_factory() as session:
            rows = (await session.execute(select(item["category"].astext, item["series"].astext).distinct())).all()
            characteristics = (await session.execute(select(
                characteristic_code, characteristic_value,
            ).select_from(ProductSnapshot).join(characteristic, true()).where(
                characteristic_code.in_(["NOMINALNYY_TOK", "NOMINALNAYA_OTKLYUCHAYUSHCHAYA_SPOSOBNOST", "TORGOVAYA_MARKA"]),
            ).distinct())).all()
            document_types = (await session.scalars(select(document_type).select_from(ProductSnapshot).join(
                document, true(),
            ).where(document_type.is_not(None), document_type != "").distinct())).all()
        by_code: dict[str, set[str]] = {
            "NOMINALNYY_TOK": set(),
            "NOMINALNAYA_OTKLYUCHAYUSHCHAYA_SPOSOBNOST": set(),
            "TORGOVAYA_MARKA": set(),
        }
        for code, value in characteristics:
            if code in by_code and value:
                by_code[code].add(value)
        return {
            "catalog_scope": "demo_subset",
            "categories": sorted({category for category, _ in rows if category}),
            "series": sorted({series for _, series in rows if series}),
            "current": sorted(by_code["NOMINALNYY_TOK"]),
            "breaking_capacity": sorted(by_code["NOMINALNAYA_OTKLYUCHAYUSHCHAYA_SPOSOBNOST"]),
            "brands": sorted(by_code["TORGOVAYA_MARKA"], key=str.casefold),
            "document_types": sorted({kind for kind in document_types if kind}),
        }

    async def find_analogs(
        self, product_id: int, required_quantity: Any = None, limit: int = 5,
    ) -> dict[str, Any]:
        source = await self.get_product(product_id)
        keys = ANALOG_KEYS.get(source["category"])
        source_props = {c["code"]: c["value"] for c in source["characteristics"]}
        if not keys or any("NOMINALNYY_TOK" in w for w in source["data_warnings"]):
            return {"source_product_id": product_id, "items": [], "compatibility_note": "Критичные свойства не подтверждены"}
        if any(not source_props.get(key) for key in keys):
            return {"source_product_id": product_id, "items": [], "compatibility_note": "Недостаточно характеристик для подбора"}
        needed = _decimal(required_quantity) if required_quantity is not None else Decimal("1")
        if needed is None or needed <= 0:
            needed = Decimal("1")
        candidates = (await self.search_catalog(filters={"category": source["category"], "available_only": True}, limit=20))["items"]
        matches = []
        for item in candidates:
            if item["id"] == source["id"] or item["price"] is None or item["unit"] is None:
                continue
            if (_decimal(item["available_quantity"]) or Decimal("0")) < needed:
                continue
            if any("NOMINALNYY_TOK" in w for w in item["data_warnings"]):
                continue
            props = {c["code"]: c["value"] for c in item["characteristics"]}
            if all(props.get(key) and _comp(props[key]) == _comp(source_props[key]) for key in keys):
                matches.append({"product": item, "matching_characteristics": [
                    {"code": key, "name": PRODUCT_FIELDS[key], "value": props[key]} for key in keys
                ]})
        return {"source_product_id": product_id, "items": matches[:max(1, min(int(limit), 5))],
                "compatibility_note": "Совпадают указанные свойства; окончательную пригодность применения нужно проверить"}

    async def get_purchase_terms(self, topic: str | None = None) -> dict[str, Any]:
        facts = json.loads((DATA_DIR / "purchase_terms.json").read_text(encoding="utf-8"))
        if topic and topic in ("payment", "delivery", "pickup"):
            return {"source_url": facts["source_url"], "checked_at": facts["checked_at"], topic: facts[topic]}
        return facts
