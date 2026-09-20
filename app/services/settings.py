from __future__ import annotations

import sqlite3

from app.storage.db import DEFAULT_SETTINGS
from app.storage.repositories import fetch_one


def get_setting(conn: sqlite3.Connection, key: str) -> str:
    row = fetch_one(conn, "SELECT value FROM settings WHERE key = ?", (key,))
    if row is None:
        conn.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
            (key, DEFAULT_SETTINGS.get(key, "")),
        )
        conn.commit()
        return DEFAULT_SETTINGS.get(key, "")
    return row["value"]


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        """
        INSERT INTO settings (key, value) VALUES (?, ?)
        ON CONFLICT (key) DO UPDATE SET value = excluded.value
        """,
        (key, value),
    )
    conn.commit()


def get_all_settings(conn: sqlite3.Connection) -> dict[str, str]:
    rows = fetch_all_settings(conn)
    merged = dict(DEFAULT_SETTINGS)
    merged.update(rows)
    return merged


def fetch_all_settings(conn: sqlite3.Connection) -> dict[str, str]:
    cur = conn.execute("SELECT key, value FROM settings")
    return {r["key"]: r["value"] for r in cur.fetchall()}