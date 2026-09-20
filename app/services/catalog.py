from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass

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


def normalize_sku(sku: str) -> str:
    return re.sub(r"\s+", " ", sku.strip().upper())


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