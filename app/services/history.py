from __future__ import annotations

import sqlite3

from app.storage.repositories import fetch_all, rows_to_dicts


def product_prices(
    conn: sqlite3.Connection,
    product_id: int,
    date_from: str = "",
    date_to: str = "",
) -> list[dict]:
    conds = ["pr.product_id = ?"]
    params: list = [product_id]
    if date_from:
        conds.append("pr.price_date >= ?")
        params.append(date_from)
    if date_to:
        conds.append("pr.price_date <= ?")
        params.append(date_to)

    sql = f"""
        SELECT pr.price, pr.price_date,
               COALESCE(s.name, 'Разовый файл') AS source_name,
               pr.source_id, pr.external_key, pr.load_id
        FROM prices pr
        LEFT JOIN sources s ON s.id = pr.source_id
        WHERE {" AND ".join(conds)}
        ORDER BY pr.price_date, pr.id
    """
    return rows_to_dicts(fetch_all(conn, sql, tuple(params)))