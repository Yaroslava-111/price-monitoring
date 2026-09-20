from __future__ import annotations

import hashlib
import io
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime

import pandas as pd

from app.core import match as match_core
from app.core.normalize import normalize_name, normalize_sku
from app.services import catalog as catalog_service
from app.services import matching
from app.services import sources as sources_service
from app.storage.repositories import fetch_all, fetch_one, row_to_dict

ALLOWED_EXTENSIONS = (".csv", ".xlsx")


class LoadError(ValueError):
    pass


@dataclass
class LoadResult:
    load_id: int
    status: str
    total_rows: int
    ok_rows: int
    error_rows: int
    pending_rows: int
    duplicate_rows: int
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "load_id": self.load_id,
            "status": self.status,
            "total_rows": self.total_rows,
            "ok_rows": self.ok_rows,
            "error_rows": self.error_rows,
            "pending_rows": self.pending_rows,
            "duplicate_rows": self.duplicate_rows,
            "errors": self.errors,
        }


def _read_setting(conn: sqlite3.Connection, key: str, default: str) -> str:
    row = fetch_one(conn, "SELECT value FROM settings WHERE key = ?", (key,))
    if row is None:
        return default
    return row["value"]


def _detect_encoding(data: bytes) -> str:
    try:
        data.decode("utf-8-sig")
        return "utf-8"
    except UnicodeDecodeError:
        return "cp1251"


def read_upload(
    data: bytes,
    filename: str,
    max_mb: int,
    max_rows: int,
) -> tuple[pd.DataFrame, str]:
    name_lower = filename.lower()
    if not name_lower.endswith(ALLOWED_EXTENSIONS):
        raise LoadError(
            f"Недопустимый формат: {filename}. Только .csv или .xlsx."
        )
    if len(data) > max_mb * 1024 * 1024:
        raise LoadError(f"Файл превышает лимит {max_mb} МБ.")

    if name_lower.endswith(".xlsx"):
        df = pd.read_excel(io.BytesIO(data))
        encoding = "xlsx"
    else:
        encoding = _detect_encoding(data)
        df = pd.read_csv(io.StringIO(data.decode(encoding)), sep=None, engine="python")
        if df.empty:
            df = pd.read_csv(
                io.StringIO(data.decode(encoding)), sep=";", engine="python"
            )

    df.columns = [str(c).strip() for c in df.columns]
    if len(df) > max_rows:
        raise LoadError(f"Файл превышает лимит {max_rows} строк.")
    if len(df) == 0:
        raise LoadError("Файл не содержит строк.")
    return df, encoding


def _norm_header(value: str) -> str:
    text = normalize_name(value)
    return re.sub(r"\s|\W", "", text)


HEADER_MAP = {
    "sku": {"sku", "артикул", "артикулы", "код", "кодтовара", "code", "externalkey", "id"},
    "name": {"name", "название", "наименование", "товар", "товары", "наименованиетовара", "title", "product", "productname"},
    "price": {"price", "цена", "ценаруб", "ценарубли", "стоимость", "закупочнаяцена", "закупка", "cost", "pricerub"},
    "date": {"date", "дата", "датапрайса", "датацены", "pricedate", "ддммгггг", "датадокумента"},
    "currency": {"currency", "валюта"},
}


def detect_headers(df: pd.DataFrame) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for col in df.columns:
        hv = _norm_header(col)
        for role, variants in HEADER_MAP.items():
            if hv in variants and role not in mapping:
                mapping[role] = col
                break
    return mapping


def _parse_price(value) -> float | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip().replace(" ", "").replace(",", ".")
    s = re.sub(r"[^\d.+-]", "", s)
    if s in ("", ".", "-"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _parse_date(value) -> str | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    s = str(value).strip()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%d.%m.%y"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return None


RUB_CURRENCIES = {"rub", "руб", "рубль", "рубли", "рубля", "р", "₽"}


def _normalize_currency(value) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    s = str(value).strip()
    if s == "":
        return ""
    t = re.sub(r"\s", "", s).lower()
    if t in RUB_CURRENCIES:
        return "RUB"
    return t


def _existing_keys(
    conn: sqlite3.Connection, source_id: int | None
) -> set[tuple[int | None, str, str]]:
    rows = fetch_all(
        conn,
        "SELECT external_key, price_date, source_id FROM prices",
    )
    keys: set[tuple[int | None, str, str]] = set()
    for row in rows:
        sid = row["source_id"]
        if source_id is None:
            if sid is not None:
                continue
            keys.add((None, row["external_key"], row["price_date"]))
        else:
            if sid != source_id:
                continue
            keys.add((source_id, row["external_key"], row["price_date"]))
    return keys


def run_import(
    conn: sqlite3.Connection,
    data: bytes,
    filename: str,
    scope: str,
    source_id: int | None = None,
    run_by: str = "manual",
    price_date_override: str | None = None,
) -> LoadResult:
    if scope not in ("competitor", "supplier"):
        raise LoadError("Неверный контур данных.")
    if run_by not in ("manual", "agent"):
        raise LoadError("Неизвестный источник запуска.")

    if source_id is not None:
        source = sources_service.get_source(conn, source_id)
        if source is None:
            raise LoadError("Источник не найден.")
        if not sources_service.is_approved(conn, source_id):
            raise LoadError(
                f"Источник «{source.name}» не разрешён (статус {source.status})."
            )
        if source.scope != scope:
            raise LoadError("Источник не относится к выбранному контуру.")

    max_mb = int(_read_setting(conn, "max_upload_mb", "20"))
    max_rows = int(_read_setting(conn, "max_upload_rows", "50000"))
    df, encoding = read_upload(data, filename, max_mb, max_rows)

    headers = detect_headers(df)
    price_col = headers.get("price")
    sku_col = headers.get("sku")
    name_col = headers.get("name")
    date_col = headers.get("date")
    currency_col = headers.get("currency")

    if price_col is None:
        raise LoadError(
            "Не найдена колонка с ценой (ожидалась «цена»/«price»)."
        )
    if sku_col is None and name_col is None:
        raise LoadError(
            "Не найдена колонка с артикулом (SKU) или названием товара."
        )

    auto_threshold = float(_read_setting(conn, "auto_threshold", "90"))
    manual_threshold = float(_read_setting(conn, "manual_threshold", "70"))
    rejected_pairs = matching.list_rejected_pairs(conn)
    products = catalog_service.list_products(conn, active_only=True)
    product_dicts = [
        {"id": p.id, "sku": p.sku, "name": p.name, "is_active": p.is_active}
        for p in products
    ]

    existing = _existing_keys(conn, source_id)

    file_hash = hashlib.sha256(data).hexdigest()
    cur = conn.execute(
        """
        INSERT INTO loads (source_id, scope, kind, file_hash, file_name, run_by)
        VALUES (?, ?, 'csv_upload', ?, ?, ?)
        """,
        (source_id, scope, file_hash, filename, run_by),
    )
    load_id = cur.lastrowid
    conn.commit()

    total = 0
    ok_rows = 0
    error_rows = 0
    pending_rows = 0
    duplicate_rows = 0
    errors: list[str] = []

    for idx, row in df.iterrows():
        total += 1
        line_no = idx + 2

        raw_sku = normalize_sku(str(row[sku_col])) if sku_col else ""
        raw_name = str(row[name_col]).strip() if name_col else ""
        external_sku = raw_sku if raw_sku else ""
        external_name = raw_name if raw_name else normalize_name(external_sku)

        if currency_col:
            cur_code = _normalize_currency(row[currency_col])
            if cur_code and cur_code != "RUB":
                errors.append(
                    f"строка {line_no}: валюта «{cur_code}» — поддерживаются только рубли (RUB)."
                )
                error_rows += 1
                continue

        price = _parse_price(row[price_col])
        if price is None:
            errors.append(f"строка {line_no}: не удалось распознать цену.")
            error_rows += 1
            continue
        if price < 0:
            errors.append(f"строка {line_no}: отрицательная цена.")
            error_rows += 1
            continue

        if price_date_override:
            price_date = price_date_override
        elif date_col:
            price_date = _parse_date(row[date_col])
            if price_date is None:
                errors.append(f"строка {line_no}: не распознана дата.")
                error_rows += 1
                continue
        else:
            price_date = date.today().isoformat()

        if not external_sku and not external_name:
            errors.append(
                f"строка {line_no}: нет ни артикула, ни названия товара."
            )
            error_rows += 1
            continue

        key = (source_id, external_sku, price_date)
        if key in existing:
            duplicate_rows += 1
            continue

        result = match_core.match(
            external_sku=external_sku,
            external_name=external_name,
            products=product_dicts,
            auto_threshold=auto_threshold,
            manual_threshold=manual_threshold,
            rejected_pairs=rejected_pairs,
        )

        if not result.matched or result.status == match_core.STATUS_PENDING:
            if result.matched and result.product_id is not None:
                matching.upsert_mapping(
                    conn,
                    product_id=result.product_id,
                    external_key=external_sku,
                    external_name=external_name,
                    method=result.method or match_core.METHOD_FUZZY,
                    similarity=result.similarity,
                    status=match_core.STATUS_PENDING,
                    price=price,
                    price_date=price_date,
                    load_id=load_id,
                )
                pending_rows += 1
            else:
                errors.append(
                    f"строка {line_no}: товар не сопоставлен с каталогом"
                    f" (порог < {int(manual_threshold)}%)."
                )
                error_rows += 1
            continue

        product_id = result.product_id
        mapping_product_for_price = product_id
        if result.status == match_core.STATUS_AUTO:
            matching.upsert_mapping(
                conn,
                product_id=product_id,  # type: ignore[arg-type]
                external_key=external_sku,
                external_name=external_name,
                method=result.method or match_core.METHOD_FUZZY,
                similarity=result.similarity,
                status=match_core.STATUS_AUTO,
                price=price,
                price_date=price_date,
                load_id=load_id,
            )

        conn.execute(
            """
            INSERT INTO prices
                (product_id, source_id, load_id, price, price_date,
                 external_key, raw_name)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                mapping_product_for_price,
                source_id,
                load_id,
                price,
                price_date,
                external_sku,
                external_name,
            ),
        )
        existing.add(key)
        ok_rows += 1

    if error_rows == 0 and ok_rows == 0 and pending_rows == 0 and duplicate_rows == 0:
        status = "success"
    elif error_rows == 0:
        status = "success"
    else:
        status = "partial"

    conn.execute(
        """
        UPDATE loads
        SET finished_at = datetime('now'), status = ?, total_rows = ?,
            ok_rows = ?, error_rows = ?, errors_summary = ?
        WHERE id = ?
        """,
        (
            status,
            total,
            ok_rows,
            error_rows,
            str(errors),
            load_id,
        ),
    )
    conn.commit()

    return LoadResult(
        load_id=load_id,
        status=status,
        total_rows=total,
        ok_rows=ok_rows,
        error_rows=error_rows,
        pending_rows=pending_rows,
        duplicate_rows=duplicate_rows,
        errors=errors,
    )


def get_load(conn: sqlite3.Connection, load_id: int) -> dict | None:
    row = fetch_one(
        conn,
        """
        SELECT l.*, s.name AS source_name, s.scope AS source_scope
        FROM loads l
        LEFT JOIN sources s ON s.id = l.source_id
        WHERE l.id = ?
        """,
        (load_id,),
    )
    return row_to_dict(row)


def list_loads(conn: sqlite3.Connection, limit: int = 20) -> list[dict]:
    rows = fetch_all(
        conn,
        """
        SELECT l.id, l.scope, l.status, l.file_name, l.total_rows,
               l.ok_rows, l.error_rows, l.started_at, l.finished_at,
               s.name AS source_name
        FROM loads l
        LEFT JOIN sources s ON s.id = l.source_id
        ORDER BY l.id DESC
        LIMIT ?
        """,
        (limit,),
    )
    return [dict(r) for r in rows]