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

    p1 = catalog_service.create_product(
        conn, "SKU-1", "Кофе Арабика 250г", 100.0, category="Кофе"
    )
    p2 = catalog_service.create_product(
        conn, "SKU-2", "Чай зелёный 100г", 200.0, category="Чай"
    )
    src = sources_service.create_source(
        conn, name="К1", scope="competitor", kind="csv_upload",
        origin_url="https://k1.example", terms_agreed=True,
    )
    load_id = conn.execute(
        "INSERT INTO loads (source_id, scope, kind, status) VALUES (?, 'competitor', 'csv_upload', 'success')",
        (src.id,),
    ).lastrowid
    conn.commit()

    for p in (p1, p2):
        matching_service.upsert_mapping(
            conn,
            product_id=p.id,
            external_key=p.sku,
            external_name=p.name,
            method="exact",
            similarity=100.0,
            status="confirmed",
        )
        price = p.own_price * 0.6 if p.sku == "SKU-1" else p.own_price * 0.98
        conn.execute(
            """
            INSERT INTO prices (product_id, source_id, load_id, price, price_date, external_key)
            VALUES (?, ?, ?, ?, '2026-09-10', ?)
            """,
            (p.id, src.id, load_id, price, p.sku),
        )
    conn.commit()
    conn.close()


def test_report_screen_empty(conn):
    at = AppTest.from_file(MAIN_SCRIPT, default_timeout=30)
    at.run()
    at.radio[0].set_value("Отчёт: конкуренты")
    at.run()
    assert not list(at.exception)


def test_report_shows_only_below_threshold(conn):
    _seed(conn)
    at = AppTest.from_file(MAIN_SCRIPT, default_timeout=30)
    at.run()
    at.radio[0].set_value("Отчёт: конкуренты")
    at.run()
    assert not list(at.exception)

    threshold_slider = at.slider[0]
    assert threshold_slider.value == 5

    df = None
    for d in at.dataframe:
        if "SKU" in str(d.value.columns):
            df = d.value
            break
    assert df is not None
    assert {"SKU-1"} == set(df["SKU"])
    assert "SKU-2" not in set(df["SKU"])
    assert "К1" in set(df["Источник"])


def test_report_slider_changes_results(conn):
    _seed(conn)
    at = AppTest.from_file(MAIN_SCRIPT, default_timeout=30)
    at.run()
    at.radio[0].set_value("Отчёт: конкуренты")
    at.run()

    slider = at.slider[0]
    slider.set_value(52)
    at.run()
    assert not list(at.exception)

    assert any("Нет позиций" in str(i.value) for i in at.info)


def test_report_download_button(conn):
    _seed(conn)
    at = AppTest.from_file(MAIN_SCRIPT, default_timeout=30)
    at.run()
    at.radio[0].set_value("Отчёт: конкуренты")
    at.run()
    assert not list(at.exception)

    assert any("Скачать CSV" in b.label for b in at.get("download_button"))


def test_report_category_filter(conn):
    _seed(conn)
    at = AppTest.from_file(MAIN_SCRIPT, default_timeout=30)
    at.run()
    at.radio[0].set_value("Отчёт: конкуренты")
    at.run()

    category_select = at.selectbox[0]
    assert "(все категории)" in category_select.options
    category_select.set_value("Чай")
    at.run()
    assert not list(at.exception)

    assert any("Нет позиций" in str(i.value) for i in at.info)