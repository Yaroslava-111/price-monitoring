"""Уведомления в Telegram о критических отклонениях цен конкурентов.

Отправляется не по расписанию, а сразу после импорта: как только в
`prices` появляются новые строки из контура «конкуренты», для них
проверяется отклонение от собственной цены. Идемпотентность импорта
(уникальный ключ источник+товар+дата) не даёт повторно уведомить о том же
при повторной загрузке того же файла — новых строк просто не появится.

Только строки с реальным `product_id` попадают в `prices` за один проход
импорта (`app/services/loads.py`) — товары со спорным сопоставлением ждут
подтверждения и цену не получают, поэтому здесь не нужно заново проверять
статус мэппинга.
"""

from __future__ import annotations

import sqlite3

from app.core.report import clamp_threshold
from app.services import settings as settings_service
from app.services import telegram
from app.storage.repositories import fetch_all

MAX_ROWS_IN_MESSAGE = 20


def config(conn: sqlite3.Connection) -> telegram.TelegramConfig:
    return telegram.TelegramConfig(
        bot_token=settings_service.get_secret(conn, "telegram_bot_token"),
        chat_id=settings_service.get_setting(conn, "telegram_chat_id"),
    )


def alert_threshold(conn: sqlite3.Connection) -> float:
    raw = settings_service.get_setting(conn, "telegram_alert_threshold_pct")
    return clamp_threshold(float(raw or "15"))


def find_critical_rows(
    conn: sqlite3.Connection, load_id: int, threshold_pct: float
) -> list[dict]:
    """Строки этой загрузки, где конкурент дешевле хотя бы на threshold_pct."""
    threshold_pct = clamp_threshold(threshold_pct)
    rows = fetch_all(
        conn,
        """
        SELECT p.sku, p.name AS product_name, p.own_price, pr.price
        FROM prices pr
        JOIN products p ON p.id = pr.product_id
        WHERE pr.load_id = ? AND p.own_price > 0
          AND (pr.price - p.own_price) * 100.0 <= -? * p.own_price
        ORDER BY (pr.price - p.own_price) / p.own_price ASC
        """,
        (load_id, threshold_pct),
    )
    result = []
    for r in rows:
        own = float(r["own_price"])
        price = float(r["price"])
        result.append(
            {
                "sku": r["sku"],
                "product_name": r["product_name"],
                "own_price": own,
                "price": price,
                "deviation_pct": (price - own) / own * 100.0,
            }
        )
    return result


def build_alert_text(rows: list[dict], threshold_pct: float) -> str:
    lines = [
        f"Критические отклонения цен конкурентов (порог {threshold_pct:.0f}%):",
        "",
    ]
    for r in rows[:MAX_ROWS_IN_MESSAGE]:
        lines.append(
            f"- {r['sku']} {r['product_name']}: "
            f"{r['price']:.2f} ₽ против {r['own_price']:.2f} ₽ "
            f"({r['deviation_pct']:+.1f}%)"
        )
    extra = len(rows) - MAX_ROWS_IN_MESSAGE
    if extra > 0:
        lines.append(f"…и ещё {extra}.")
    return "\n".join(lines)


def _load_scope(conn: sqlite3.Connection, load_id: int) -> str | None:
    row = conn.execute("SELECT scope FROM loads WHERE id = ?", (load_id,)).fetchone()
    return row["scope"] if row else None


def notify_load(conn: sqlite3.Connection, load_id: int) -> str | None:
    """Проверяет свежую загрузку и, если нужно, шлёт уведомление.

    Возвращает None, если уведомление не требовалось: Telegram не настроен,
    контур не «конкуренты», или ни одна строка не превысила порог. Иначе —
    короткую сводку об отправке. Ошибку самой отправки (`TelegramError`)
    не глотает — её показывает вызывающий экран, чтобы владелец видел,
    что данные загружены, а уведомление не ушло.
    """
    if _load_scope(conn, load_id) != "competitor":
        return None

    cfg = config(conn)
    if not cfg.configured:
        return None

    threshold = alert_threshold(conn)
    rows = find_critical_rows(conn, load_id, threshold)
    if not rows:
        return None

    telegram.send_message(cfg, build_alert_text(rows, threshold))
    word = "позиция" if len(rows) == 1 else ("позиции" if len(rows) < 5 else "позиций")
    return f"Уведомление в Telegram отправлено: {len(rows)} {word}."
