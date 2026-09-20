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


def _seed(conn):
    from app.services import catalog as catalog_service
    from app.services import matching as matching_service
    from app.services import sources as sources_service

    p = catalog_service.create_product(
        conn, "SKU-1", "Кофе 250г", 200.0, category="Кофе"
    )
    comp = sources_service.create_source(
        conn, name="К9", scope="competitor", kind="csv_upload",
        origin_url="https://k9.example", terms_agreed=True,
    )
    sup = sources_service.create_source(
        conn, name="С9", scope="supplier", kind="csv_upload",
        origin_url="https://s9.example", terms_agreed=True,
    )
    load = conn.execute(
        "INSERT INTO loads (source_id, scope, kind, status) VALUES (?, 'competitor', 'csv_upload', 'success')",
        (comp.id,),
    ).lastrowid
    load_s = conn.execute(
        "INSERT INTO loads (source_id, scope, kind, status) VALUES (?, 'supplier', 'csv_upload', 'success')",
        (sup.id,),
    ).lastrowid
    conn.commit()

    for src, lid, price, d in (
        (comp, load, 190.0, "2026-09-01"),
        (comp, load, 185.0, "2026-09-05"),
        (sup, load_s, 150.0, "2026-09-02"),
    ):
        matching_service.upsert_mapping(
            conn,
            product_id=p.id,
            external_key=f"EXT-{src.id}",
            external_name=f"EXT-{src.id}",
            method="exact",
            similarity=100.0,
            status="confirmed",
        )
        conn.execute(
            """
            INSERT INTO prices (product_id, source_id, load_id, price, price_date, external_key)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (p.id, src.id, lid, price, d, f"EXT-{src.id}"),
        )
    conn.commit()
    conn.close()
    return p


def test_history_screen_empty(conn):
    at = AppTest.from_file(MAIN_SCRIPT, default_timeout=30)
    at.run()
    at.radio[0].set_value("История")
    at.run()
    assert not list(at.exception)


def test_history_screen_table_and_download(conn):
    _seed(conn)
    at = AppTest.from_file(MAIN_SCRIPT, default_timeout=30)
    at.run()
    at.radio[0].set_value("История")
    at.run()
    assert not list(at.exception)

    table = None
    for d in at.dataframe:
        if "Источник" in str(d.value.columns):
            table = d.value
            break
    assert table is not None
    assert len(table) == 3
    assert "К9" in table["Источник"].tolist()
    assert "С9" in table["Источник"].tolist()

    assert any("Скачать CSV" in b.label for b in at.get("download_button"))