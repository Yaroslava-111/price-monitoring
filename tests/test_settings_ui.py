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
    at.radio[0].set_value("Настройки")
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
    return next(b for b in at.button if b.label == "💾 Сохранить пороги")


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