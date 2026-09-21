import pytest
from streamlit.testing.v1 import AppTest

from app.storage import db as db_module

MAIN_SCRIPT = str(db_module.BASE_DIR / "app" / "main.py")


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    monkeypatch.setattr(db_module, "_db_path", lambda: tmp_path / "test.db")
    connection = db_module.init_db()
    yield connection
    connection.close()


def _open_settings(at: AppTest) -> AppTest:
    at.switch_page("screens/settings.py")
    at.run()
    return at


def _threshold_inputs(at: AppTest):
    auto = next(
        n for n in at.number_input if n.label == "Автоматический порог (auto), %"
    )
    manual = next(
        n for n in at.number_input if n.label == "Ручной порог (manual), %"
    )
    return auto, manual


def _save_button(at: AppTest):
    return next(b for b in at.button if b.label == "Сохранить пороги")


def test_settings_defaults(conn):
    at = AppTest.from_file(MAIN_SCRIPT, default_timeout=30)
    at.run()
    at = _open_settings(at)
    assert not list(at.exception)
    auto, manual = _threshold_inputs(at)
    assert auto.value == 90
    assert manual.value == 70


def test_settings_update_thresholds(conn):
    at = AppTest.from_file(MAIN_SCRIPT, default_timeout=30)
    at.run()
    at = _open_settings(at)

    auto, manual = _threshold_inputs(at)
    auto.set_value(85)
    manual.set_value(55)
    _save_button(at).click()
    at.run()
    assert not list(at.exception)

    c2 = db_module.init_db()
    try:
        rows = c2.execute("SELECT key, value FROM settings").fetchall()
        cfg = {r["key"]: r["value"] for r in rows}
        assert cfg["auto_threshold"] == "85"
        assert cfg["manual_threshold"] == "55"
    finally:
        c2.close()


def test_settings_rejects_manual_gte_auto(conn):
    at = AppTest.from_file(MAIN_SCRIPT, default_timeout=30)
    at.run()
    at = _open_settings(at)

    auto, manual = _threshold_inputs(at)
    manual.set_value(95)
    at.run()
    errors = [e.value for e in at.error]
    assert any(
        "Ручной порог должен быть строго меньше" in e for e in errors
    )

    c2 = db_module.init_db()
    try:
        row = c2.execute(
            "SELECT value FROM settings WHERE key = 'manual_threshold'"
        ).fetchone()
        assert row["value"] == "70"
    finally:
        c2.close()

def _telegram_inputs(at: AppTest):
    token = next(t for t in at.text_input if t.label == "Токен бота")
    chat_id = next(t for t in at.text_input if t.label == "Chat ID")
    threshold = next(n for n in at.number_input if n.label == "Критический порог, %")
    return token, chat_id, threshold


def test_telegram_tab_defaults(conn):
    at = AppTest.from_file(MAIN_SCRIPT, default_timeout=30)
    at.run()
    at = _open_settings(at)

    assert not list(at.exception)
    token, chat_id, threshold = _telegram_inputs(at)
    assert token.value == ""
    assert chat_id.value == ""
    assert threshold.value == 15
    assert any("Уведомления выключены" in w.value for w in at.warning)


def test_telegram_save_persists_settings(conn):
    from app.services import settings as settings_service

    at = AppTest.from_file(MAIN_SCRIPT, default_timeout=30)
    at.run()
    at = _open_settings(at)

    token, chat_id, threshold = _telegram_inputs(at)
    token.set_value("123:abc")
    chat_id.set_value("777")
    threshold.set_value(25)
    next(b for b in at.button if b.label == "Сохранить параметры Telegram").click()
    at.run()

    assert not list(at.exception)
    assert any("сохранены" in s.value for s in at.get("success"))

    conn2 = db_module.init_db()
    try:
        assert settings_service.get_setting(conn2, "telegram_bot_token") == "123:abc"
        assert settings_service.get_setting(conn2, "telegram_chat_id") == "777"
        assert settings_service.get_setting(conn2, "telegram_alert_threshold_pct") == "25"
    finally:
        conn2.close()

    at = _open_settings(at)
    assert any("Уведомления включены" in s.value for s in at.get("success"))
    assert any("более чем на 25%" in s.value for s in at.get("success"))


def test_telegram_test_button_reports_send_error(conn, monkeypatch):
    from app.services import settings as settings_service
    from app.services import telegram

    settings_service.set_setting(conn, "telegram_bot_token", "123:abc")
    settings_service.set_setting(conn, "telegram_chat_id", "777")
    conn.commit()

    def boom(config):
        raise telegram.TelegramError("HTTP 401. токен бота не принят")

    monkeypatch.setattr(telegram, "send_test", boom)

    at = AppTest.from_file(MAIN_SCRIPT, default_timeout=30)
    at.run()
    at = _open_settings(at)
    next(b for b in at.button if b.label == "Отправить тестовое сообщение").click()
    at.run()

    assert not list(at.exception)
    assert any("Не отправлено" in e.value for e in at.error)
    assert any("токен бота не принят" in e.value for e in at.error)


def test_telegram_test_button_reports_success(conn, monkeypatch):
    from app.services import settings as settings_service
    from app.services import telegram

    settings_service.set_setting(conn, "telegram_bot_token", "123:abc")
    settings_service.set_setting(conn, "telegram_chat_id", "777")
    conn.commit()

    sent = {}
    monkeypatch.setattr(telegram, "send_test", lambda cfg: sent.setdefault("ok", cfg))

    at = AppTest.from_file(MAIN_SCRIPT, default_timeout=30)
    at.run()
    at = _open_settings(at)
    next(b for b in at.button if b.label == "Отправить тестовое сообщение").click()
    at.run()

    assert not list(at.exception)
    assert any("Сообщение отправлено" in s.value for s in at.get("success"))
    assert sent["ok"].chat_id == "777"
