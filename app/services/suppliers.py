from __future__ import annotations

import sqlite3

from app.core.suppliers import to_stats
from app.storage.repositories import (
    RAW_SOURCE_ID,
    fetch_all,
    rows_to_dicts,
    scope_filter_sql,
)

__all__ = [
    "RAW_SOURCE_ID",
    "has_raw_supplier_prices",
    "latest_prices",
    "list_products_with_supplier_prices",
    "list_supplier_categories",
    "list_supplier_sources",
    "product_series",
    "product_summary",
    "stats_for_product",
]


def latest_prices(
    conn: sqlite3.Connection,
    date_from: str = "",
    date_to: str = "",
    category: str = "",
    source_id: int | None = None,
) -> list[dict]:
    """Последняя цена по каждому товару×поставщик (только сопоставленные поставщики)."""
    # Подзапрос ограничен контуром поставщиков: разовые цены конкурентов и
    # поставщиков имеют одинаковый source_id IS NULL и иначе слились бы в одну
    # группу, где более поздняя дата вытесняет нужную строку.
    window_conds: list[str] = [scope_filter_sql("prq", "supplier")]
    window_params: list = []
    if date_from:
        window_conds.append("prq.price_date >= ?")
        window_params.append(date_from)
    if date_to:
        window_conds.append("prq.price_date <= ?")
        window_params.append(date_to)

    conds: list[str] = [scope_filter_sql("pr", "supplier")]
    row_params: list = []
    if category:
        conds.append("p.category = ?")
        row_params.append(category)
    if source_id is not None:
        if source_id == RAW_SOURCE_ID:
            conds.append("pr.source_id IS NULL")
        else:
            conds.append("pr.source_id = ?")
            row_params.append(source_id)

    sql = f"""
        SELECT p.id AS product_id, p.sku, p.name AS product_name,
               p.category, p.own_price,
               pr.source_id,
               COALESCE(s.name, 'Разовый файл') AS source_name,
               pr.price, pr.price_date AS price_date
        FROM prices pr
        JOIN products p ON p.id = pr.product_id
        LEFT JOIN sources s ON s.id = pr.source_id
        JOIN (
            SELECT product_id, source_id, MAX(price_date) AS max_date
            FROM prices prq
            WHERE {" AND ".join(window_conds)}
            GROUP BY product_id, source_id
        ) latest
          ON latest.product_id = pr.product_id
         AND latest.source_id IS pr.source_id
         AND latest.max_date = pr.price_date
        WHERE {" AND ".join(conds)}
        ORDER BY p.sku, source_name
    """
    # Порядок параметров — по порядку плейсхолдеров в тексте: сначала подзапрос.
    return rows_to_dicts(fetch_all(conn, sql, tuple(window_params + row_params)))


def product_summary(
    conn: sqlite3.Connection,
    product_id: int,
    date_from: str = "",
    date_to: str = "",
) -> list[dict]:
    """По товару: по каждому поставщику последняя цена и мин/макс/средняя за период."""
    conds = ["pr.product_id = ?", scope_filter_sql("pr", "supplier")]
    params2: list = [product_id]
    if date_from:
        conds.append("pr.price_date >= ?")
        params2.append(date_from)
    if date_to:
        conds.append("pr.price_date <= ?")
        params2.append(date_to)
    select_where = " AND ".join(conds)

    # «Последняя цена» ищется отдельным подзапросом, поэтому контур нужно
    # повторить и там — иначе для разового файла подхватится цена конкурента.
    last_price_where = (
        "pl.product_id = pr.product_id AND pl.source_id IS pr.source_id"
        f" AND {scope_filter_sql('pl', 'supplier')}"
    )
    if date_from:
        last_price_where += " AND pl.price_date >= ?"
    if date_to:
        last_price_where += " AND pl.price_date <= ?"
    # порядок параметров: подзапросы в SELECT стоят до основного WHERE
    sub_params: list = []
    for _ in range(2):
        if date_from:
            sub_params.append(date_from)
        if date_to:
            sub_params.append(date_to)

    sql = f"""
        SELECT pr.source_id,
               COALESCE(s.name, 'Разовый файл') AS source_name,
               (
                   SELECT price FROM prices pl
                   WHERE {last_price_where}
                   ORDER BY pl.price_date DESC, pl.id DESC
                   LIMIT 1
               ) AS last_price,
               (
                   SELECT price_date FROM prices pl
                   WHERE {last_price_where}
                   ORDER BY pl.price_date DESC, pl.id DESC
                   LIMIT 1
               ) AS last_date,
               MIN(pr.price) AS min_price,
               MAX(pr.price) AS max_price,
               AVG(pr.price) AS avg_price,
               MIN(pr.price_date) AS first_date
        FROM prices pr
        LEFT JOIN sources s ON s.id = pr.source_id
        WHERE {select_where}
        GROUP BY pr.source_id, s.name
        ORDER BY source_name
    """
    final_params = sub_params + params2
    return rows_to_dicts(fetch_all(conn, sql, tuple(final_params)))


def list_products_with_supplier_prices(
    conn: sqlite3.Connection,
    category: str = "",
) -> list[dict]:
    conds = [scope_filter_sql("pr", "supplier")]
    params: list = []
    if category:
        conds.append("p.category = ?")
        params.append(category)
    sql = f"""
        SELECT DISTINCT p.id, p.sku, p.name, p.category, p.own_price
        FROM prices pr
        JOIN products p ON p.id = pr.product_id
        WHERE {" AND ".join(conds)}
        ORDER BY p.name
    """
    return rows_to_dicts(fetch_all(conn, sql, tuple(params)))


def list_supplier_categories(conn: sqlite3.Connection) -> list[str]:
    rows = fetch_all(
        conn,
        f"""
        SELECT DISTINCT p.category FROM products p
        WHERE p.category <> '' AND EXISTS (
            SELECT 1 FROM prices pr
            WHERE pr.product_id = p.id AND {scope_filter_sql("pr", "supplier")}
        )
        ORDER BY p.category
        """,
    )
    return [r["category"] for r in rows]


def list_supplier_sources(conn: sqlite3.Connection) -> list[dict]:
    rows = fetch_all(
        conn,
        """
        SELECT DISTINCT s.id, s.name FROM sources s
        WHERE s.scope = 'supplier' AND EXISTS (
            SELECT 1 FROM prices pr WHERE pr.source_id = s.id
        )
        ORDER BY s.name
        """,
    )
    return [dict(r) for r in rows]


def has_raw_supplier_prices(conn: sqlite3.Connection) -> bool:
    """Есть ли цены поставщиков, загруженные разовым файлом (без источника)."""
    sql = f"""
        SELECT 1 FROM prices pr
        WHERE pr.source_id IS NULL AND {scope_filter_sql("pr", "supplier")}
        LIMIT 1
    """
    return bool(fetch_all(conn, sql))


def product_series(
    conn: sqlite3.Connection,
    product_id: int,
) -> list[dict]:
    """Весь временной ряд цен по товару с атрибуцией источника (для графика)."""
    rows = fetch_all(
        conn,
        f"""
        SELECT pr.price, pr.price_date, pr.source_id,
               COALESCE(s.name, 'Разовый файл') AS source_name
        FROM prices pr
        LEFT JOIN sources s ON s.id = pr.source_id
        WHERE pr.product_id = ? AND {scope_filter_sql("pr", "supplier")}
        ORDER BY pr.price_date, pr.id
        """,
        (product_id,),
    )
    return rows_to_dicts(rows)


def stats_for_product(
    conn: sqlite3.Connection,
    product_id: int,
    date_from: str = "",
    date_to: str = "",
):
    stats = to_stats(product_summary(conn, product_id, date_from, date_to))
    return stats
