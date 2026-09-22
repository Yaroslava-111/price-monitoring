from __future__ import annotations

import os
import sqlite3

import app.env  # noqa: F401 — поднимает .env в os.environ

from app.storage.db import DEFAULT_SETTINGS
from app.storage.repositories import fetch_one

# Секреты, которые читаются из переменных окружения (.env) в приоритете
# над значениями из базы: если переменная задана, значение в БД не читается
# и при сохранении настроек туда не пишется.
SECRET_ENV = {
    "agent_key": "MONITORING_AGENT_API_KEY",
    "telegram_bot_token": "MONITORING_TELEGRAM_BOT_TOKEN",
}


def env_secret(key: str) -> str:
    """Значение секрета из окружения (без пробелов по краям) или пустая строка."""
    env_name = SECRET_ENV.get(key)
    if not env_name:
        return ""
    return os.environ.get(env_name, "").strip()


def get_secret(conn: sqlite3.Connection, key: str) -> str:
    """Секрет: из окружения, если задан; иначе из БД (режим совместимости)."""
    value = env_secret(key)
    if value:
        return value
    return get_setting(conn, key)


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