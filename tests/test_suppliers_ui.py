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
        conn, "SKU-A", "Молоко 1л", 80.0, category="Молочка"
    )
    sup1 = sources_service.create_source(
        conn, name="С1", scope="supplier", kind="csv_upload",
        origin_url="https://sup1.example", terms_agreed=True,
    )
    sup2 = sources_service.create_source(
        conn, name="С2", scope="supplier", kind="csv_upload",
        origin_url="https://sup2.example", terms_agreed=True,
    )
    load1 = conn.execute(
        "INSERT INTO loads (source_id, scope, kind, status) VALUES (?, 'supplier', 'csv_upload', 'success')",
        (sup1.id,),
    ).lastrowid
    load2 = conn.execute(
        "INSERT INTO loads (source_id, scope, kind, status) VALUES (?, 'supplier', 'csv_upload', 'success')",
        (sup2.id,),
    ).lastrowid
    conn.commit()

    for src, load, price in ((sup1, load1, 75.0), (sup2, load2, 72.0)):
        matching_service.upsert_mapping(
            conn,
            product_id=p1.id,
            external_key=f"EXT-{src.name}",
            external_name=f"EXT-{src.name}",
            method="exact",
            similarity=100.0,
            status="confirmed",
        )
        conn.execute(
            """
            INSERT INTO prices (product_id, source_id, load_id, price, price_date, external_key)
            VALUES (?, ?, ?, ?, '2026-09-10', ?)
            """,
            (p1.id, src.id, load, price, f"EXT-{src.name}"),
        )
    conn.commit()
    conn.close()


def test_suppliers_screen_empty(conn):
    at = AppTest.from_file(MAIN_SCRIPT, default_timeout=30)
    at.run()
    at.radio[0].set_value("Прайсы: поставщики")
    at.run()
    assert not list(at.exception)


def test_suppliers_matrix_and_summary(conn):
    _seed(conn)
    at = AppTest.from_file(MAIN_SCRIPT, default_timeout=30)
    at.run()
    at.radio[0].set_value("Прайсы: поставщики")
    at.run()
    assert not list(at.exception)

    matrix = None
    for d in at.dataframe:
        if "Поставщик" in str(d.value.columns):
            matrix = d.value
            break
    assert matrix is not None
    assert "С1" in matrix["Поставщик"].tolist()
    assert "С2" in matrix["Поставщик"].tolist()
    assert matrix[matrix["Поставщик"] == "С1"]["Последняя цена, ₽"].iloc[0] == pytest.approx(75.0)

    summary = None
    for d in at.dataframe:
        if "Последняя цена, ₽" in str(d.value.columns) and d is not matrix:
            summary = d.value
            break
    assert summary is not None

    ok = False
    for s in at.success:
        if "С2" in s.value and "72.00" in s.value:
            ok = True
            break
    assert ok


def test_suppliers_product_select(conn):
    _seed(conn)
    at = AppTest.from_file(MAIN_SCRIPT, default_timeout=30)
    at.run()
    at.radio[0].set_value("Прайсы: поставщики")
    at.run()

    product_box = None
    for sb in at.selectbox:
        if sb.key == "suppliers_product":
            product_box = sb
            break
    assert product_box is not None
    assert "SKU-A — Молоко 1л" in product_box.options