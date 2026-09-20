from __future__ import annotations

import sqlite3

from app.core.normalize import normalize_name
from app.storage.repositories import fetch_all, fetch_one, rows_to_dicts


def list_pending(conn: sqlite3.Connection) -> list[dict]:
    rows = fetch_all(
        conn,
        """
        SELECT m.id, m.product_id, m.external_key, m.external_name,
               m.method, m.similarity, m.status, m.ai_recommended,
               m.created_at, p.sku AS product_sku, p.name AS product_name
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
) -> None:
    conn.execute(
        """
        INSERT INTO mappings
            (product_id, external_key, external_name, method, similarity, status)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT (external_key, product_id) DO UPDATE SET
            external_name = excluded.external_name,
            method = excluded.method,
            similarity = excluded.similarity,
            status = excluded.status
        """,
        (
            product_id,
            external_key,
            external_name,
            method,
            similarity,
            status,
        ),
    )
    conn.commit()


def get_mapping(conn: sqlite3.Connection, mapping_id: int) -> dict | None:
    row = fetch_one(
        conn, "SELECT * FROM mappings WHERE id = ?", (mapping_id,)
    )
    return dict(row) if row is not None else None


def mark_confirmed(conn: sqlite3.Connection, mapping_id: int) -> None:
    conn.execute(
        """
        UPDATE mappings SET status = 'confirmed', reviewed_at = datetime('now')
        WHERE id = ?
        """,
        (mapping_id,),
    )
    conn.commit()


def mark_rejected(conn: sqlite3.Connection, mapping_id: int) -> None:
    conn.execute(
        """
        UPDATE mappings SET status = 'rejected', reviewed_at = datetime('now')
        WHERE id = ?
        """,
        (mapping_id,),
    )
    conn.commit()