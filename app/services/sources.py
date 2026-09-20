from __future__ import annotations

import sqlite3
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.storage.repositories import fetch_all, fetch_one, row_to_dict

NAME_MAX_LEN = 100
URL_MAX_LEN = 500
TEXT_MAX_LEN = 2000
SCOPES = ("competitor", "supplier")
KINDS = ("csv_upload", "api", "price_feed")
STATUSES = ("approved", "blocked", "expired")


class SourceError(ValueError):
    pass


@dataclass
class Source:
    id: int
    name: str
    scope: str
    kind: str
    origin_url: str
    terms_hash: str
    terms_agreed: bool
    allowed_fields: str
    usage_limits: str
    min_refresh_minutes: int
    requires_attribution: bool
    status: str
    approved_at: str | None
    review_period_days: int


def _validate_url(value: str) -> list[str]:
    errors: list[str] = []
    if not value.strip():
        errors.append("URL источника обязателен.")
        return errors
    parsed = urllib.parse.urlparse(value.strip())
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        errors.append("URL источника должен быть валидным http(s) адресом.")
    return errors


def _validate_string(value: str, label: str, max_len: int) -> list[str]:
    errors: list[str] = []
    if value is None or str(value).strip() == "":
        errors.append(f"{label}: значение не может быть пустым.")
    elif len(str(value).strip()) > max_len:
        errors.append(f"{label}: превышает максимум {max_len} символов.")
    return errors


def validate_source(
    name: str,
    scope: str,
    kind: str,
    origin_url: str = "",
    min_refresh_minutes: int = 0,
    review_period_days: int = 365,
) -> list[str]:
    errors: list[str] = []
    errors.extend(_validate_string(name, "Название", NAME_MAX_LEN))
    if scope not in SCOPES:
        errors.append("Неверный контур данных.")
    if kind not in KINDS:
        errors.append("Неверный тип источника.")
    if kind in ("api", "price_feed"):
        errors.extend(_validate_url(origin_url))
    elif origin_url.strip():
        errors.extend(_validate_url(origin_url))
    if origin_url and len(origin_url.strip()) > URL_MAX_LEN:
        errors.append(f"URL: превышает максимум {URL_MAX_LEN} символов.")
    return errors


def _row_to_source(row: sqlite3.Row) -> Source:
    return Source(
        id=row["id"],
        name=row["name"],
        scope=row["scope"],
        kind=row["kind"],
        origin_url=row["origin_url"],
        terms_hash=row["terms_hash"],
        terms_agreed=bool(row["terms_agreed"]),
        allowed_fields=row["allowed_fields"],
        usage_limits=row["usage_limits"],
        min_refresh_minutes=row["min_refresh_minutes"],
        requires_attribution=bool(row["requires_attribution"]),
        status=row["status"],
        approved_at=row["approved_at"],
        review_period_days=row["review_period_days"],
    )


def _apply_status(source: Source) -> str:
    if not source.terms_agreed:
        return "blocked"
    if source.approved_at and source.review_period_days > 0:
        approved = datetime.fromisoformat(source.approved_at)
        if datetime.now() > approved + timedelta(days=source.review_period_days):
            return "expired"
    return "approved"


def list_sources(
    conn: sqlite3.Connection,
    scope: str | None = None,
    include_blocked: bool = True,
) -> list[Source]:
    query = "SELECT * FROM sources"
    conds: list[str] = []
    params: list = []
    if scope in SCOPES:
        conds.append("scope = ?")
        params.append(scope)
    if not include_blocked:
        conds.append("status = 'approved'")
    if conds:
        query += " WHERE " + " AND ".join(conds)
    query += " ORDER BY name"
    sources = [_row_to_source(r) for r in fetch_all(conn, query, tuple(params))]
    for source in sources:
        current = _apply_status(source)
        if source.status != current:
            conn.execute(
                "UPDATE sources SET status = ? WHERE id = ?",
                (current, source.id),
            )
            conn.commit()
            source.status = current
    return sources


def get_source(conn: sqlite3.Connection, source_id: int) -> Source | None:
    row = fetch_one(conn, "SELECT * FROM sources WHERE id = ?", (source_id,))
    if row is None:
        return None
    source = _row_to_source(row)
    current = _apply_status(source)
    if source.status != current:
        conn.execute(
            "UPDATE sources SET status = ? WHERE id = ?", (current, source.id)
        )
        conn.commit()
        source.status = current
    return source


def create_source(
    conn: sqlite3.Connection,
    name: str,
    scope: str,
    kind: str,
    origin_url: str = "",
    terms_hash: str = "",
    terms_agreed: bool = False,
    allowed_fields: str = "",
    usage_limits: str = "",
    min_refresh_minutes: int = 0,
    requires_attribution: bool = True,
    review_period_days: int = 365,
) -> Source:
    errors = validate_source(
        name, scope, kind, origin_url, min_refresh_minutes, review_period_days
    )
    if errors:
        raise SourceError("; ".join(errors))

    approved_at = None
    status = "blocked"
    if terms_agreed:
        approved_at = datetime.now().isoformat(timespec="seconds")
        status = "approved"

    cur = conn.execute(
        """
        INSERT INTO sources (
            name, scope, kind, origin_url, terms_hash, terms_agreed,
            allowed_fields, usage_limits, min_refresh_minutes,
            requires_attribution, status, approved_at, review_period_days
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            name.strip(),
            scope,
            kind,
            origin_url.strip(),
            terms_hash.strip(),
            int(bool(terms_agreed)),
            allowed_fields.strip(),
            usage_limits.strip(),
            int(min_refresh_minutes),
            int(bool(requires_attribution)),
            status,
            approved_at,
            int(review_period_days),
        ),
    )
    conn.commit()
    return get_source(conn, cur.lastrowid)  # type: ignore[arg-type]


def update_source(
    conn: sqlite3.Connection,
    source_id: int,
    name: str | None = None,
    scope: str | None = None,
    kind: str | None = None,
    origin_url: str | None = None,
    terms_hash: str | None = None,
    terms_agreed: bool | None = None,
    allowed_fields: str | None = None,
    usage_limits: str | None = None,
    min_refresh_minutes: int | None = None,
    requires_attribution: bool | None = None,
    review_period_days: int | None = None,
) -> Source:
    existing = get_source(conn, source_id)
    if existing is None:
        raise SourceError("Источник не найден.")

    new_name = name.strip() if name is not None else existing.name
    new_scope = scope or existing.scope
    new_kind = kind or existing.kind
    new_url = origin_url.strip() if origin_url is not None else existing.origin_url
    new_terms = terms_agreed if terms_agreed is not None else existing.terms_agreed
    new_refresh = (
        int(min_refresh_minutes)
        if min_refresh_minutes is not None
        else existing.min_refresh_minutes
    )
    new_review = (
        int(review_period_days)
        if review_period_days is not None
        else existing.review_period_days
    )

    errors = validate_source(
        new_name, new_scope, new_kind, new_url, new_refresh, new_review
    )
    if errors:
        raise SourceError("; ".join(errors))

    approved_at = existing.approved_at
    if new_terms and not existing.terms_agreed:
        approved_at = datetime.now().isoformat(timespec="seconds")

    new_status = _apply_status(
        Source(
            id=existing.id,
            name=new_name,
            scope=new_scope,
            kind=new_kind,
            origin_url=new_url,
            terms_hash=terms_hash.strip() if terms_hash is not None else existing.terms_hash,
            terms_agreed=new_terms,
            allowed_fields=allowed_fields.strip() if allowed_fields is not None else existing.allowed_fields,
            usage_limits=usage_limits.strip() if usage_limits is not None else existing.usage_limits,
            min_refresh_minutes=new_refresh,
            requires_attribution=requires_attribution if requires_attribution is not None else existing.requires_attribution,
            status=existing.status,
            approved_at=approved_at,
            review_period_days=new_review,
        )
    )

    conn.execute(
        """
        UPDATE sources
        SET name = ?, scope = ?, kind = ?, origin_url = ?, terms_hash = ?,
            terms_agreed = ?, allowed_fields = ?, usage_limits = ?,
            min_refresh_minutes = ?, requires_attribution = ?,
            status = ?, approved_at = ?, review_period_days = ?
        WHERE id = ?
        """,
        (
            new_name,
            new_scope,
            new_kind,
            new_url,
            terms_hash.strip() if terms_hash is not None else existing.terms_hash,
            int(bool(new_terms)),
            allowed_fields.strip() if allowed_fields is not None else existing.allowed_fields,
            usage_limits.strip() if usage_limits is not None else existing.usage_limits,
            new_refresh,
            int(bool(requires_attribution))
            if requires_attribution is not None
            else int(existing.requires_attribution),
            new_status,
            approved_at,
            new_review,
            source_id,
        ),
    )
    conn.commit()
    return get_source(conn, source_id)  # type: ignore[return-value]


def delete_source(conn: sqlite3.Connection, source_id: int) -> None:
    conn.execute("DELETE FROM sources WHERE id = ?", (source_id,))
    conn.commit()


def is_approved(conn: sqlite3.Connection, source_id: int) -> bool:
    source = get_source(conn, source_id)
    if source is None:
        return False
    return source.status == "approved"