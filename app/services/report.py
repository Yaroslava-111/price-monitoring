from __future__ import annotations

import sqlite3

from app.core.report import clamp_threshold
from app.storage.repositories import (
    RAW_SOURCE_ID,
    fetch_all,
    rows_to_dicts,
    scope_filter_sql,
)

STATUSES = "('confirmed', 'auto')"

__all__ = [
    "RAW_SOURCE_ID",
    "STATUSES",
    "list_categories",
    "list_competitor_sources",
    "list_deviations",
    "total_deviations",
]

MAPPED_SQL = """
    EXISTS (
        SELECT 1 FROM mappings m
        WHERE m.external_key = pr.external_key
          AND m.product_id = p.id
          AND m.status IN ('confirmed', 'auto')
    )
"""


def list_deviations(
    conn: sqlite3.Connection,
    threshold: float,
    category: str = "",
    source_id: int | None = None,
    date_from: str = "",
    date_to: str = "",
) -> list[dict]:
    threshold = clamp_threshold(threshold)

    # Подзапрос «последняя цена» тоже ограничен контуром: иначе разовая цена
    # конкурента и разовая цена поставщика (обе с source_id IS NULL) попадают
    # в одну группу и более поздняя вытесняет нужную.
    window_conds: list[str] = [scope_filter_sql("prq", "competitor")]
    window_params: list = []

    row_conds: list[str] = [
        "p.own_price > 0",
        scope_filter_sql("pr", "competitor"),
        MAPPED_SQL,
        "(pr.price - p.own_price) * 100.0 <= -? * p.own_price",
    ]
    row_params: list = [threshold]

    if date_from:
        window_conds.append("prq.price_date >= ?")
        window_params.append(date_from)
        row_conds.append("pr.price_date >= ?")
        row_params.append(date_from)
    if date_to:
        window_conds.append("prq.price_date <= ?")
        window_params.append(date_to)
        row_conds.append("pr.price_date <= ?")
        row_params.append(date_to)
    if category:
        row_conds.append("p.category = ?")
        row_params.append(category)
    if source_id is not None:
        if source_id == RAW_SOURCE_ID:
            row_conds.append("pr.source_id IS NULL")
        else:
            row_conds.append("pr.source_id = ?")
            row_params.append(source_id)

    sql = f"""
        SELECT p.id AS product_id, p.sku, p.name AS product_name, p.category,
               p.own_price,
               pr.price,
               (pr.price - p.own_price) * 100.0
                 / NULLIF(p.own_price, 0) AS deviation_pct,
               pr.price - p.own_price AS delta_rub,
               COALESCE(s.name, 'Разовый файл') AS source_name,
               pr.source_id,
               pr.price_date
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
        WHERE {" AND ".join(row_conds)}
        ORDER BY deviation_pct ASC, p.sku
    """
    # Плейсхолдеры связываются по позиции в тексте запроса, а подзапрос
    # стоит раньше внешнего WHERE — поэтому его параметры идут первыми.
    rows = fetch_all(conn, sql, tuple(window_params + row_params))
    return rows_to_dicts(rows)


def total_deviations(
    conn: sqlite3.Connection,
    category: str = "",
    source_id: int | None = None,
    date_from: str = "",
    date_to: str = "",
) -> int:
    conds: list[str] = [
        "p.own_price > 0",
        scope_filter_sql("pr", "competitor"),
        MAPPED_SQL,
    ]
    params: list = []
    if category:
        conds.append("p.category = ?")
        params.append(category)
    if source_id is not None:
        if source_id == RAW_SOURCE_ID:
            conds.append("pr.source_id IS NULL")
        else:
            conds.append("pr.source_id = ?")
            params.append(source_id)
    if date_from:
        conds.append("pr.price_date >= ?")
        params.append(date_from)
    if date_to:
        conds.append("pr.price_date <= ?")
        params.append(date_to)

    sql = f"""
        SELECT COUNT(*) AS c
        FROM prices pr
        JOIN products p ON p.id = pr.product_id
        WHERE {" AND ".join(conds)}
    """
    return int(fetch_all(conn, sql, tuple(params))[0]["c"])


def list_categories(conn: sqlite3.Connection) -> list[str]:
    rows = fetch_all(
        conn,
        """
        SELECT DISTINCT category FROM products
        WHERE category <> '' ORDER BY category
        """,
    )
    return [r["category"] for r in rows]


def list_competitor_sources(conn: sqlite3.Connection) -> list[dict]:
    rows = fetch_all(
        conn,
        """
        SELECT id, name FROM sources
        WHERE scope = 'competitor' ORDER BY name
        """,
    )
    return [dict(r) for r in rows]
