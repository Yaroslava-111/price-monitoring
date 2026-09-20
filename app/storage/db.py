from __future__ import annotations

import os
import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]


def _db_path() -> Path:
    override = os.environ.get("MONITORING_DB")
    if override:
        return Path(override)
    return BASE_DIR / "db" / "monitoring.db"

DEFAULT_SETTINGS: dict[str, str] = {
    "auto_threshold": "90",
    "manual_threshold": "70",
    "deviation_threshold_pct": "5",
    "agent_endpoint": "",
    "agent_key": "",
    "agent_model": "",
    "max_upload_mb": "20",
    "max_upload_rows": "50000",
}

MIGRATIONS: list[str] = [
    """
    CREATE TABLE IF NOT EXISTS products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sku TEXT NOT NULL UNIQUE,
        name TEXT NOT NULL,
        category TEXT NOT NULL DEFAULT '',
        own_price REAL NOT NULL DEFAULT 0 CHECK (own_price >= 0),
        is_active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS sources (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        scope TEXT NOT NULL CHECK (scope IN ('competitor', 'supplier')),
        kind TEXT NOT NULL CHECK (kind IN ('csv_upload', 'api', 'price_feed')),
        origin_url TEXT NOT NULL DEFAULT '',
        terms_hash TEXT NOT NULL DEFAULT '',
        terms_agreed INTEGER NOT NULL DEFAULT 0,
        allowed_fields TEXT NOT NULL DEFAULT '',
        usage_limits TEXT NOT NULL DEFAULT '',
        min_refresh_minutes INTEGER NOT NULL DEFAULT 0,
        requires_attribution INTEGER NOT NULL DEFAULT 1,
        status TEXT NOT NULL DEFAULT 'blocked'
            CHECK (status IN ('approved', 'blocked', 'expired')),
        approved_at TEXT,
        review_period_days INTEGER NOT NULL DEFAULT 365,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS loads (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source_id INTEGER REFERENCES sources(id) ON DELETE SET NULL,
        scope TEXT NOT NULL CHECK (scope IN ('competitor', 'supplier')),
        kind TEXT NOT NULL CHECK (kind IN ('csv_upload', 'api', 'price_feed', 'agent_manual')),
        file_hash TEXT NOT NULL DEFAULT '',
        file_name TEXT NOT NULL DEFAULT '',
        started_at TEXT NOT NULL DEFAULT (datetime('now')),
        finished_at TEXT,
        status TEXT NOT NULL DEFAULT 'running'
            CHECK (status IN ('running', 'success', 'partial', 'failed')),
        total_rows INTEGER NOT NULL DEFAULT 0,
        ok_rows INTEGER NOT NULL DEFAULT 0,
        error_rows INTEGER NOT NULL DEFAULT 0,
        errors_summary TEXT NOT NULL DEFAULT '[]',
        run_by TEXT NOT NULL DEFAULT 'manual'
            CHECK (run_by IN ('manual', 'agent'))
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS prices (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
        source_id INTEGER REFERENCES sources(id) ON DELETE CASCADE,
        load_id INTEGER NOT NULL REFERENCES loads(id) ON DELETE CASCADE,
        price REAL NOT NULL CHECK (price >= 0),
        price_date TEXT NOT NULL,
        external_key TEXT NOT NULL DEFAULT '',
        raw_name TEXT NOT NULL DEFAULT ''
    );
    CREATE UNIQUE INDEX IF NOT EXISTS idx_prices_uniq
        ON prices(source_id, external_key, price_date);
    CREATE INDEX IF NOT EXISTS idx_prices_product ON prices(product_id);
    CREATE INDEX IF NOT EXISTS idx_prices_date ON prices(price_date);
    """,
    """
    CREATE TABLE IF NOT EXISTS mappings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
        external_key TEXT NOT NULL,
        external_name TEXT NOT NULL DEFAULT '',
        method TEXT NOT NULL CHECK (method IN ('exact', 'fuzzy', 'manual')),
        similarity REAL,
        status TEXT NOT NULL DEFAULT 'pending'
            CHECK (status IN ('auto', 'pending', 'confirmed', 'rejected')),
        ai_recommended INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        reviewed_at TEXT,
        UNIQUE (external_key, product_id)
    );
    CREATE INDEX IF NOT EXISTS idx_mappings_status ON mappings(status);
    """,
    """
    CREATE TABLE IF NOT EXISTS recommendations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
        source_id INTEGER REFERENCES sources(id) ON DELETE CASCADE,
        rec_type TEXT NOT NULL
            CHECK (rec_type IN ('price_change', 'source_switch', 'match')),
        payload TEXT NOT NULL DEFAULT '{}',
        status TEXT NOT NULL DEFAULT 'proposed'
            CHECK (status IN ('proposed', 'applied', 'dismissed')),
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS agent_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        task TEXT NOT NULL,
        source_id INTEGER REFERENCES sources(id) ON DELETE SET NULL,
        started_at TEXT NOT NULL DEFAULT (datetime('now')),
        status TEXT NOT NULL CHECK (status IN ('running', 'success', 'failed')),
        error TEXT NOT NULL DEFAULT ''
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    """,
]


def get_connection() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    applied = {
        row["version"]
        for row in conn.execute("SELECT version FROM schema_migrations")
    }
    for idx, script in enumerate(MIGRATIONS, start=1):
        if idx in applied:
            continue
        with conn:
            conn.executescript(script)
            conn.execute(
                "INSERT INTO schema_migrations (version) VALUES (?)", (idx,)
            )


def seed_settings(conn: sqlite3.Connection) -> None:
    for key, value in DEFAULT_SETTINGS.items():
        conn.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
            (key, value),
        )
    conn.commit()


def init_db() -> sqlite3.Connection:
    conn = get_connection()
    migrate(conn)
    seed_settings(conn)
    return conn