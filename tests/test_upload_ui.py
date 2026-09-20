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


def test_upload_screen_renders(conn):
    at = AppTest.from_file(MAIN_SCRIPT, default_timeout=30)
    at.run()
    assert not list(at.exception)
    assert len(at.radio) >= 1


def test_upload_import_full_flow(conn):
    from app.services import catalog as catalog_service
    from app.services import sources as sources_service

    catalog_service.create_product(conn, "COF-1", "Кофе Арабика 250г", 300.0)
    sources_service.create_source(
        conn,
        name="К1",
        scope="competitor",
        kind="csv_upload",
        origin_url="https://k1.example",
        terms_agreed=True,
    )
    conn.close()

    at = AppTest.from_file(MAIN_SCRIPT, default_timeout=30)
    at.run()
    at.radio[0].set_value("Загрузка")
    at.run()
    assert len(at.file_uploader) == 1

    at.file_uploader[0].set_value(
        (
            "k1.csv",
            "sku,цена,дата\nCOF-1,299.90,2026-09-01\n".encode("utf-8"),
            "text/csv",
        )
    )
    at.run()
    assert not list(at.exception)

    import_button = next(b for b in at.button if b.label == "Запустить импорт")
    import_button.click()
    at.run()
    assert not list(at.exception)

    success_msgs = [m.value for m in at.get("success")]
    assert any("Импорт завершён" in s for s in success_msgs)

    conn2 = db_module.init_db()
    try:
        loads = conn2.execute(
            "SELECT status, ok_rows, error_rows FROM loads ORDER BY id DESC"
        ).fetchone()
        assert loads["status"] == "success"
        assert loads["ok_rows"] == 1
        assert conn2.execute("SELECT COUNT(*) n FROM prices").fetchone()["n"] == 1
    finally:
        conn2.close()


def test_upload_rejects_duplicate_source_upload(conn):
    from app.services import catalog as catalog_service
    from app.services import loads as loads_service
    from app.services import sources as sources_service

    catalog_service.create_product(conn, "COF-1", "Кофе Арабика 250г", 300.0)
    src = sources_service.create_source(
        conn,
        name="К1",
        scope="competitor",
        kind="csv_upload",
        origin_url="https://k1.example",
        terms_agreed=True,
    )
    data = "sku,цена\nCOF-1,10\n".encode("utf-8")
    first = loads_service.run_import(conn, data, "k1.csv", "competitor", src.id)
    second = loads_service.run_import(conn, data, "k1.csv", "competitor", src.id)
    assert first.ok_rows == 1
    assert second.ok_rows == 0
    assert second.duplicate_rows == 1
    conn.close()