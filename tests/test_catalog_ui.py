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


def _open_catalog(at: AppTest) -> AppTest:
    at.switch_page("screens/catalog.py")
    at.run()
    return at


def test_catalog_screen_empty(conn):
    at = AppTest.from_file(MAIN_SCRIPT, default_timeout=30)
    at.run()
    at = _open_catalog(at)
    assert not list(at.exception)


def test_catalog_import_csv_flow(conn):
    at = AppTest.from_file(MAIN_SCRIPT, default_timeout=30)
    at.run()
    at = _open_catalog(at)

    file_uploaders = at.file_uploader
    assert any(f.label == "Файл каталога (.csv)" for f in file_uploaders)

    uploader = next(f for f in file_uploaders if f.label == "Файл каталога (.csv)")
    csv_data = "sku,название,категория,цена\nCOF-1,Кофе Арабика,Кофе,400\n".encode("utf-8")
    uploader.set_value(("catalog.csv", csv_data, "text/csv"))
    at.run()
    assert not list(at.exception)

    import_btn = next(b for b in at.button if b.key == "catalog_import_run")
    import_btn.click()
    at.run()
    assert not list(at.exception)

    conn2 = db_module.init_db()
    try:
        rows = conn2.execute("SELECT sku, name FROM products").fetchall()
        assert len(rows) == 1
        assert rows[0]["sku"] == "COF-1"
        assert rows[0]["name"] == "Кофе Арабика"
    finally:
        conn2.close()