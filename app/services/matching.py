from __future__ import annotations

import sqlite3

from app.core.normalize import normalize_name
from app.storage.repositories import fetch_all, fetch_one, rows_to_dicts


class MatchingError(ValueError):
    pass


def list_pending(conn: sqlite3.Connection) -> list[dict]:
    rows = fetch_all(
        conn,
        """
        SELECT m.id, m.product_id, m.external_key, m.external_name,
               m.method, m.similarity, m.status, m.ai_recommended,
               m.price, m.price_date, m.created_at,
               p.sku AS product_sku, p.name AS product_name
        FROM mappings m
        JOIN products p ON p.id = m.product_id
        WHERE m.status = 'pending'
        ORDER BY m.similarity DESC, m.external_name
        """,
    )
    return rows_to_dicts(rows)


def list_rejected_pairs(conn: sqlite3.Connection) -> set[tuple[int, str]]:
    rows = fetch_all(
        conn,
        """
        SELECT product_id, external_name FROM mappings
        WHERE status = 'rejected'
        """,
    )
    return {(r["product_id"], normalize_name(r["external_name"])) for r in rows}


def upsert_mapping(
    conn: sqlite3.Connection,
    product_id: int,
    external_key: str,
    external_name: str,
    method: str,
    similarity: float,
    status: str,
    price: float | None = None,
    price_date: str | None = None,
    load_id: int | None = None,
    ai_recommended: bool = False,
) -> None:
    conn.execute(
        """
        INSERT INTO mappings
            (product_id, external_key, external_name, method, similarity, status,
             price, price_date, load_id, ai_recommended)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (external_key, product_id) DO UPDATE SET
            external_name = excluded.external_name,
            method = excluded.method,
            similarity = excluded.similarity,
            status = excluded.status,
            price = excluded.price,
            price_date = excluded.price_date,
            load_id = excluded.load_id,
            ai_recommended = excluded.ai_recommended
        """,
        (
            product_id,
            external_key,
            external_name,
            method,
            similarity,
            status,
            price,
            price_date,
            load_id,
            int(bool(ai_recommended)),
        ),
    )
    conn.commit()


def get_mapping(conn: sqlite3.Connection, mapping_id: int) -> dict | None:
    row = fetch_one(
        conn, "SELECT * FROM mappings WHERE id = ?", (mapping_id,)
    )
    return dict(row) if row is not None else None


def _insert_price_from_mapping(
    conn: sqlite3.Connection, mapping: dict
) -> None:
    price = mapping.get("price")
    price_date = mapping.get("price_date")
    if price is None or price_date is None:
        return
    load = None
    if mapping.get("load_id"):
        row = fetch_one(
            conn, "SELECT source_id FROM loads WHERE id = ?", (mapping["load_id"],)
        )
        if row is not None:
            load = dict(row)
    source_id = load["source_id"] if load else None
    exists = fetch_one(
        conn,
        """
        SELECT 1 FROM prices
        WHERE source_id IS ? AND external_key = ? AND price_date = ?
        """,
        (
            source_id,
            mapping["external_key"],
            price_date,
        ),
    )
    if exists:
        return
    conn.execute(
        """
        INSERT INTO prices
            (product_id, source_id, load_id, price, price_date,
             external_key, raw_name)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            mapping["product_id"],
            source_id,
            mapping.get("load_id"),
            price,
            price_date,
            mapping["external_key"],
            mapping["external_name"],
        ),
    )


def confirm_mapping(
    conn: sqlite3.Connection,
    mapping_id: int,
    product_id: int | None = None,
) -> None:
    mapping = get_mapping(conn, mapping_id)
    if mapping is None:
        raise MatchingError("Сопоставление не найдено.")
    if mapping["status"] == "rejected":
        raise MatchingError("Отклонённое сопоставление требует ручного подтверждения товара.")

    target_product = product_id if product_id is not None else mapping["product_id"]

    if target_product != mapping["product_id"]:
        conn.execute(
            """
            UPDATE mappings SET product_id = ? WHERE id = ?
            """,
            (target_product, mapping_id),
        )
        mapping["product_id"] = target_product

    conn.execute(
        """
        UPDATE mappings SET status = 'confirmed', reviewed_at = datetime('now')
        WHERE id = ?
        """,
        (mapping_id,),
    )
    _insert_price_from_mapping(conn, mapping)
    conn.commit()


def mark_rejected(conn: sqlite3.Connection, mapping_id: int) -> None:
    mapping = get_mapping(conn, mapping_id)
    if mapping is None:
        raise MatchingError("Сопоставление не найдено.")
    if mapping["status"] != "pending":
        raise MatchingError("Отклонить можно только ожидающее сопоставление.")
    conn.execute(
        """
        UPDATE mappings SET status = 'rejected', reviewed_at = datetime('now')
        WHERE id = ?
        """,
        (mapping_id,),
    )
    conn.commit()


def list_mappings(
    conn: sqlite3.Connection, limit: int = 100, offset: int = 0
) -> list[dict]:
    rows = fetch_all(
        conn,
        """
        SELECT m.id, m.product_id, m.external_key, m.external_name,
               m.method, m.similarity, m.status, m.ai_recommended,
               m.created_at, m.reviewed_at, p.sku AS product_sku,
               p.name AS product_name
        FROM mappings m
        JOIN products p ON p.id = m.product_id
        ORDER BY m.id DESC
        LIMIT ? OFFSET ?
        """,
        (limit, offset),
    )
    return rows_to_dicts(rows)


def count_pending(conn: sqlite3.Connection) -> int:
    row = fetch_one(
        conn, "SELECT COUNT(*) AS c FROM mappings WHERE status = 'pending'"
    )
    return int(row["c"]) if row else 0