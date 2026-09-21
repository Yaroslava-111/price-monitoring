"""Критические уведомления в Telegram: детекция и сборка текста.

Сеть подменена — `telegram.send_message` перехватывается, реальных
запросов нет.
"""

import pytest

from app.services import catalog as catalog_service
from app.services import matching as matching_service
from app.services import notifications
from app.services import settings as settings_service
from app.services import telegram
from app.storage import db as db_module

TELEGRAM = {
    "telegram_bot_token": "123:token",
    "telegram_chat_id": "42",
}


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    monkeypatch.setattr(db_module, "_db_path", lambda: tmp_path / "test.db")
    connection = db_module.init_db()
    yield connection
    connection.close()


def _configure_telegram(conn, threshold="15"):
    for key, value in TELEGRAM.items():
        settings_service.set_setting(conn, key, value)
    settings_service.set_setting(conn, "telegram_alert_threshold_pct", threshold)
    conn.commit()


def _load(conn, scope="competitor"):
    return conn.execute(
        "INSERT INTO loads (source_id, scope, kind, status) "
        "VALUES (NULL, ?, 'csv_upload', 'success')",
        (scope,),
    ).lastrowid


def _price_row(conn, product, load_id, price, external_key=None):
    key = external_key or product.sku
    matching_service.upsert_mapping(
        conn,
        product_id=product.id,
        external_key=key,
        external_name=product.name,
        method="exact",
        similarity=100.0,
        status="confirmed",
    )
    conn.execute(
        "INSERT INTO prices (product_id, source_id, load_id, price, price_date, external_key) "
        "VALUES (?, NULL, ?, ?, '2026-09-10', ?)",
        (product.id, load_id, price, key),
    )


# ---- config / threshold ----


def test_config_reads_settings(conn):
    _configure_telegram(conn)
    cfg = notifications.config(conn)
    assert cfg.bot_token == "123:token"
    assert cfg.chat_id == "42"
    assert cfg.configured


def test_config_not_configured_by_default(conn):
    assert not notifications.config(conn).configured


def test_alert_threshold_defaults_to_fifteen(conn):
    assert notifications.alert_threshold(conn) == 15.0


def test_alert_threshold_is_clamped(conn):
    settings_service.set_setting(conn, "telegram_alert_threshold_pct", "500")
    assert notifications.alert_threshold(conn) == 100.0


# ---- find_critical_rows ----


def test_finds_rows_at_or_beyond_threshold(conn):
    load_id = _load(conn)
    cheap = catalog_service.create_product(conn, "SKU-1", "Дёшево у конкурента", 100.0)
    borderline = catalog_service.create_product(conn, "SKU-2", "Ровно на пороге", 100.0)
    fine = catalog_service.create_product(conn, "SKU-3", "Чуть дешевле", 100.0)
    _price_row(conn, cheap, load_id, 70.0)  # -30%
    _price_row(conn, borderline, load_id, 85.0)  # -15%, ровно порог
    _price_row(conn, fine, load_id, 95.0)  # -5%, ниже порога
    conn.commit()

    rows = notifications.find_critical_rows(conn, load_id, threshold_pct=15.0)
    skus = [r["sku"] for r in rows]
    assert skus == ["SKU-1", "SKU-2"]  # порог включительно, сортировка по силе отклонения


def test_ignores_price_increase(conn):
    load_id = _load(conn)
    product = catalog_service.create_product(conn, "SKU-4", "Дороже конкурента", 100.0)
    _price_row(conn, product, load_id, 150.0)
    conn.commit()

    assert notifications.find_critical_rows(conn, load_id, 15.0) == []


def test_ignores_other_loads(conn):
    load_a = _load(conn)
    load_b = _load(conn)
    product = catalog_service.create_product(conn, "SKU-5", "Товар", 100.0)
    _price_row(conn, product, load_a, 50.0)
    conn.commit()

    assert notifications.find_critical_rows(conn, load_b, 15.0) == []
    assert len(notifications.find_critical_rows(conn, load_a, 15.0)) == 1


def test_ignores_products_without_own_price(conn):
    load_id = _load(conn)
    product = catalog_service.create_product(conn, "SKU-6", "Без своей цены", 0.0)
    _price_row(conn, product, load_id, 10.0)
    conn.commit()

    assert notifications.find_critical_rows(conn, load_id, 15.0) == []


# ---- build_alert_text ----


def test_alert_text_lists_all_rows_within_limit():
    rows = [
        {"sku": "A", "product_name": "Товар A", "own_price": 100.0, "price": 70.0, "deviation_pct": -30.0},
    ]
    text = notifications.build_alert_text(rows, 15.0)
    assert "порог 15%" in text
    assert "A Товар A" in text
    assert "70.00" in text and "100.00" in text
    assert "-30.0%" in text


def test_alert_text_truncates_long_lists():
    rows = [
        {
            "sku": f"S{i}",
            "product_name": "Товар",
            "own_price": 100.0,
            "price": 50.0,
            "deviation_pct": -50.0,
        }
        for i in range(25)
    ]
    text = notifications.build_alert_text(rows, 15.0)
    assert text.count("\n- ") == notifications.MAX_ROWS_IN_MESSAGE
    assert "…и ещё 5." in text


# ---- notify_load: сквозной сценарий ----


def test_notify_load_sends_when_critical_and_configured(conn, monkeypatch):
    _configure_telegram(conn)
    load_id = _load(conn, "competitor")
    product = catalog_service.create_product(conn, "SKU-7", "Товар", 100.0)
    _price_row(conn, product, load_id, 70.0)
    conn.commit()

    sent = {}
    monkeypatch.setattr(
        telegram, "send_message", lambda cfg, text: sent.update(cfg=cfg, text=text)
    )

    summary = notifications.notify_load(conn, load_id)

    assert summary == "Уведомление в Telegram отправлено: 1 позиция."
    assert sent["cfg"].chat_id == "42"
    assert "SKU-7" in sent["text"]


def test_notify_load_silent_when_telegram_not_configured(conn, monkeypatch):
    load_id = _load(conn, "competitor")
    product = catalog_service.create_product(conn, "SKU-8", "Товар", 100.0)
    _price_row(conn, product, load_id, 70.0)
    conn.commit()

    monkeypatch.setattr(
        telegram, "send_message", lambda *a, **k: pytest.fail("сеть не должна вызываться")
    )
    assert notifications.notify_load(conn, load_id) is None


def test_notify_load_silent_when_nothing_critical(conn, monkeypatch):
    _configure_telegram(conn)
    load_id = _load(conn, "competitor")
    product = catalog_service.create_product(conn, "SKU-9", "Товар", 100.0)
    _price_row(conn, product, load_id, 98.0)  # -2%, ниже порога 15%
    conn.commit()

    monkeypatch.setattr(
        telegram, "send_message", lambda *a, **k: pytest.fail("сеть не должна вызываться")
    )
    assert notifications.notify_load(conn, load_id) is None


def test_notify_load_ignores_supplier_scope(conn, monkeypatch):
    """Контур поставщиков не про конкурентные отклонения — уведомлять не нужно."""
    _configure_telegram(conn)
    load_id = _load(conn, "supplier")
    product = catalog_service.create_product(conn, "SKU-10", "Товар", 100.0)
    _price_row(conn, product, load_id, 10.0)
    conn.commit()

    monkeypatch.setattr(
        telegram, "send_message", lambda *a, **k: pytest.fail("сеть не должна вызываться")
    )
    assert notifications.notify_load(conn, load_id) is None


def test_notify_load_propagates_send_error(conn, monkeypatch):
    """Ошибку отправки не глотаем — экран должен показать её владельцу."""
    _configure_telegram(conn)
    load_id = _load(conn, "competitor")
    product = catalog_service.create_product(conn, "SKU-11", "Товар", 100.0)
    _price_row(conn, product, load_id, 70.0)
    conn.commit()

    def boom(cfg, text):
        raise telegram.TelegramError("HTTP 403. бот заблокирован")

    monkeypatch.setattr(telegram, "send_message", boom)

    with pytest.raises(telegram.TelegramError):
        notifications.notify_load(conn, load_id)


def test_repeated_import_does_not_duplicate_alert(conn, monkeypatch):
    """Идемпотентность импорта: повторный файл не создаёт новых строк —
    значит find_critical_rows для НОВОЙ загрузки просто не найдёт строк,
    если импорт целиком дублировался (проверяем логику на уровне loads.py)."""
    from app.services import loads as loads_service

    _configure_telegram(conn)
    catalog_service.create_product(conn, "DUP-1", "Товар", 100.0)
    conn.commit()

    data = "sku,цена\nDUP-1,70\n".encode("utf-8")
    first = loads_service.run_import(conn, data, "f.csv", "competitor")
    second = loads_service.run_import(conn, data, "f.csv", "competitor")

    calls = []
    monkeypatch.setattr(telegram, "send_message", lambda cfg, text: calls.append(text))

    assert notifications.notify_load(conn, first.load_id) is not None
    assert notifications.notify_load(conn, second.load_id) is None
    assert len(calls) == 1
