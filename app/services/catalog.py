from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

import pandas as pd

from app.core.normalize import normalize_sku
from app.storage.repositories import fetch_all, fetch_one, row_to_dict, rows_to_dicts

SKU_MAX_LEN = 64
NAME_MAX_LEN = 200
CATEGORY_MAX_LEN = 100
PRICE_MAX = 10**9
DEFAULT = object()


class ValidationError(ValueError):
    pass


@dataclass
class Product:
    id: int
    sku: str
    name: str
    category: str
    own_price: float
    is_active: bool


def _validate_string(value: str, label: str, max_len: int) -> list[str]:
    errors: list[str] = []
    if value is None or str(value).strip() == "":
        errors.append(f"{label}: значение не может быть пустым.")
    elif len(str(value).strip()) > max_len:
        errors.append(f"{label}: превышает максимум {max_len} символов.")
    return errors


def _validate_price(value: float) -> list[str]:
    errors: list[str] = []
    if value is None or (isinstance(value, str) and value.strip() == ""):
        errors.append("Своя цена: обязательное числовое значение.")
        return errors
    try:
        price = float(value)
    except (TypeError, ValueError):
        errors.append("Своя цена: должно быть числом.")
        return errors
    if price < 0:
        errors.append("Своя цена: не может быть отрицательной.")
    if price > PRICE_MAX:
        errors.append(f"Своя цена: превышает максимум {PRICE_MAX}.")
    return errors


def validate_product(
    sku: str, name: str, category: str = "", own_price: float | None = None
) -> list[str]:
    errors: list[str] = []
    errors.extend(_validate_string(sku, "SKU", SKU_MAX_LEN))
    errors.extend(_validate_string(name, "Название", NAME_MAX_LEN))
    if category is not None and str(category).strip() != "":
        errors.extend(_validate_string(category, "Категория", CATEGORY_MAX_LEN))
    errors.extend(_validate_price(own_price))
    return errors


def _row_to_product(row: sqlite3.Row) -> Product:
    return Product(
        id=row["id"],
        sku=row["sku"],
        name=row["name"],
        category=row["category"],
        own_price=row["own_price"],
        is_active=bool(row["is_active"]),
    )


def list_products(conn: sqlite3.Connection, active_only: bool = False) -> list[Product]:
    query = "SELECT * FROM products"
    if active_only:
        query += " WHERE is_active = 1"
    query += " ORDER BY sku, name"
    return [
        _row_to_product(r)
        for r in fetch_all(conn, query)
    ]


def get_product(conn: sqlite3.Connection, product_id: int) -> Product | None:
    row = fetch_one(conn, "SELECT * FROM products WHERE id = ?", (product_id,))
    if row is None:
        return None
    return _row_to_product(row)


def get_product_by_sku(conn: sqlite3.Connection, sku: str) -> Product | None:
    row = fetch_one(conn, "SELECT * FROM products WHERE sku = ?", (normalize_sku(sku),))
    if row is None:
        return None
    return _row_to_product(row)


def create_product(
    conn: sqlite3.Connection,
    sku: str,
    name: str,
    own_price: float,
    category: str = "",
) -> Product:
    errors = validate_product(sku, name, category, own_price)
    if errors:
        raise ValidationError("; ".join(errors))

    norm_sku = normalize_sku(sku)
    if get_product_by_sku(conn, norm_sku) is not None:
        raise ValidationError("Товар с таким SKU уже существует.")

    cur = conn.execute(
        """
        INSERT INTO products (sku, name, category, own_price, is_active)
        VALUES (?, ?, ?, ?, 1)
        """,
        (
            norm_sku,
            name.strip(),
            category.strip(),
            float(own_price),
        ),
    )
    conn.commit()
    return get_product(conn, cur.lastrowid)  # type: ignore[arg-type]


def update_product(
    conn: sqlite3.Connection,
    product_id: int,
    name: str | None = None,
    own_price: float | None = None,
    category: str | None = None,
    is_active: bool | None = None,
) -> Product:
    existing = get_product(conn, product_id)
    if existing is None:
        raise ValidationError("Товар не найден.")

    new_name = name.strip() if name is not None else existing.name
    new_category = (
        category.strip() if category is not None else existing.category
    )
    new_price = (
        float(own_price) if own_price is not None else existing.own_price
    )
    errors = validate_product(existing.sku, new_name, new_category, new_price)
    if errors:
        raise ValidationError("; ".join(errors))

    new_active = (
        int(bool(is_active)) if is_active is not None else int(existing.is_active)
    )
    conn.execute(
        """
        UPDATE products
        SET name = ?, category = ?, own_price = ?, is_active = ?,
            updated_at = datetime('now')
        WHERE id = ?
        """,
        (new_name, new_category, new_price, new_active, product_id),
    )
    conn.commit()
    return get_product(conn, product_id)  # type: ignore[return-value]


def delete_product(conn: sqlite3.Connection, product_id: int) -> None:
    conn.execute("DELETE FROM products WHERE id = ?", (product_id,))
    conn.commit()


def _norm_header(value: str) -> str:
    import re

    return re.sub(r"\s|\W", "", value.lower().strip())


HEADER_MAP = {
    "sku": {"sku", "артикул", "артикулы", "код", "кодтовара", "code", "id"},
    "name": {"name", "название", "наименование", "товар", "товары", "title", "product", "productname"},
    "category": {"category", "категория", "группа", "групптоваров", "раздел"},
    "price": {"price", "цена", "ценаруб", "ценарубли", "стоимость", "cost", "pricerub", "свояцена", "закупка", "закупочнаяцена"},
}


def _detect_encoding(data: bytes) -> str:
    try:
        data.decode("utf-8-sig")
        return "utf-8"
    except UnicodeDecodeError:
        return "cp1251"


def _parse_price_import(value) -> float | None:
    import re

    if value is None or (isinstance(value, float) and _pd_isna(value)):
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


def _pd_isna(value) -> bool:
    try:
        import pandas as pd

        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


@dataclass
class CatalogImportResult:
    added: int = 0
    skipped_sku_exists: int = 0
    errors: list[str] = field(default_factory=list)


def load_catalog_csv(
    conn: sqlite3.Connection,
    data: bytes,
    filename: str,
    max_mb: int = 20,
) -> CatalogImportResult:
    """Импорт каталога из CSV. Заголовки: SKU/артикул, название,
    цена, категория (необязательно). Повторный SKU пропускается."""
    import io

    if filename.lower().endswith(".xlsx"):
        raise ValidationError("Для каталога поддерживается только CSV.")
    if len(data) > max_mb * 1024 * 1024:
        raise ValidationError(f"Файл превышает лимит {max_mb} МБ.")

    encoding = _detect_encoding(data)
    try:
        df = pd.read_csv(io.StringIO(data.decode(encoding)), sep=None, engine="python")
        if df.empty:
            df = pd.read_csv(io.StringIO(data.decode(encoding)), sep=";", engine="python")
    except Exception as exc:  # noqa: BLE001
        raise ValidationError(f"Не удалось прочитать CSV: {exc}") from exc

    df.columns = [str(c).strip() for c in df.columns]
    if df.empty:
        raise ValidationError("Файл не содержит строк.")

    headers = {}
    for col in df.columns:
        hv = _norm_header(col)
        for role, variants in HEADER_MAP.items():
            if hv in variants and role not in headers:
                headers[role] = col
                break

    sku_col = headers.get("sku")
    name_col = headers.get("name")
    price_col = headers.get("price")
    category_col = headers.get("category")

    if sku_col is None or name_col is None or price_col is None:
        raise ValidationError(
            "Не найдены колонки SKU, названия и цены. Ожидались: sku, название, цена."
        )

    result = CatalogImportResult()
    for idx, row in df.iterrows():
        line_no = idx + 2
        try:
            sku_value = row[sku_col]
            if _pd_isna(sku_value):
                raise ValidationError("SKU: значение не может быть пустым.")
            sku = normalize_sku(str(sku_value))
            name = str(row[name_col]).strip()
            category = str(row[category_col]).strip() if category_col else ""
            price = _parse_price_import(row[price_col])
            if price is None:
                raise ValidationError("не удалось распознать цену")
            try:
                create_product(conn, sku, name, price, category)
                result.added += 1
            except ValidationError as exc:
                if "уже существует" in str(exc):
                    result.skipped_sku_exists += 1
                else:
                    raise
        except ValidationError as exc:
            result.errors.append(f"строка {line_no}: {exc}")
    conn.commit()
    return result