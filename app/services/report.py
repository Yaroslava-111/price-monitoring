from __future__ import annotations

import sqlite3

from app.core.report import clamp_threshold
from app.storage.repositories import fetch_all, rows_to_dicts

STATUSES = "('confirmed', 'auto')"


def list_deviations(
    conn: sqlite3.Connection,
    threshold: float,
    category: str = "",
    source_id: int | None = None,
    date_from: str = "",
    date_to: str = "",
) -> list[dict]:
    threshold = clamp_threshold(threshold)
    window_conds: list[str] = []
    row_conds: list[str] = [
        "p.own_price > 0",
        "s.scope = 'competitor'",
        """
        EXISTS (
            SELECT 1 FROM mappings m
            WHERE m.external_key = pr.external_key
              AND m.product_id = p.id
              AND m.status IN ('confirmed', 'auto')
        )
        """,
        f"(pr.price - p.own_price) * 100.0 <= -? * p.own_price",
    ]
    params: list = [threshold]

    if date_from:
        window_conds.append("prq.price_date >= ?")
        row_conds.append("pr.price_date >= ?")
        params.append(date_from)
        params.append(date_from)
    if date_to:
        window_conds.append("prq.price_date <= ?")
        row_conds.append("pr.price_date <= ?")
        params.append(date_to)
        params.append(date_to)
    if category:
        row_conds.append("p.category = ?")
        params.append(category)
    if source_id is not None:
        row_conds.append("pr.source_id = ?")
        params.append(source_id)

    window_sql = " AND ".join(window_conds) if window_conds else "1 = 1"
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
            WHERE {window_sql}
            GROUP BY product_id, source_id
        ) latest
          ON latest.product_id = pr.product_id
         AND latest.source_id IS pr.source_id
         AND latest.max_date = pr.price_date
        WHERE {" AND ".join(row_conds)}
        ORDER BY deviation_pct ASC, p.sku
    """
    rows = fetch_all(conn, sql, tuple(params))
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
        "s.scope = 'competitor'",
        """
        EXISTS (
            SELECT 1 FROM mappings m
            WHERE m.external_key = pr.external_key
              AND m.product_id = p.id
              AND m.status IN ('confirmed', 'auto')
        )
        """,
    ]
    params: list = []
    if category:
        conds.append("p.category = ?")
        params.append(category)
    if source_id is not None:
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
        JOIN sources s ON s.id = pr.source_id
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