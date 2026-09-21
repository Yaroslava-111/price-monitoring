from __future__ import annotations

import sqlite3

from app.storage.db import get_connection


def fetch_all(conn: sqlite3.Connection, query: str, params: tuple = ()) -> list[sqlite3.Row]:
    return conn.execute(query, params).fetchall()


def fetch_one(conn: sqlite3.Connection, query: str, params: tuple = ()) -> sqlite3.Row | None:
    return conn.execute(query, params).fetchone()


def row_to_dict(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    return dict(row)


def rows_to_dicts(rows: list[sqlite3.Row]) -> list[dict]:
    return [dict(r) for r in rows]


# Разовые файлы грузятся без регистрации источника (`prices.source_id IS NULL`),
# поэтому в UI им нужен собственный «псевдо-id» для фильтра по источнику.
RAW_SOURCE_ID = -1


def scope_filter_sql(prices_alias: str, scope: str) -> str:
    """Фрагмент WHERE: строка таблицы `prices` относится к контуру `scope`.

    Для цены из зарегистрированного источника контур берётся из `sources.scope`,
    для разового файла (`source_id IS NULL`) — из загрузки `loads.scope`.
    Фрагмент не требует джойна с `sources`, поэтому годится и для подзапросов.
    """
    if scope not in ("competitor", "supplier"):
        raise ValueError(f"Неизвестный контур: {scope}")
    alias = prices_alias
    return f"""
    (
        EXISTS (
            SELECT 1 FROM sources src
            WHERE src.id = {alias}.source_id AND src.scope = '{scope}'
        )
        OR (
            {alias}.source_id IS NULL
            AND EXISTS (
                SELECT 1 FROM loads ld
                WHERE ld.id = {alias}.load_id AND ld.scope = '{scope}'
            )
        )
    )
    """
