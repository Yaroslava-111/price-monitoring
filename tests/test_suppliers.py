import pytest

from app.core import suppliers as core_suppliers
from app.services import suppliers as supplier_service
from app.storage import db as db_module


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
    p2 = catalog_service.create_product(
        conn, "SKU-B", "Сметана 20% 400г", 120.0, category="Молочка"
    )
    sup1 = sources_service.create_source(
        conn, name="С1", scope="supplier", kind="csv_upload",
        origin_url="https://sup1.example", terms_agreed=True,
    )
    sup2 = sources_service.create_source(
        conn, name="С2", scope="supplier", kind="csv_upload",
        origin_url="https://sup2.example", terms_agreed=True,
    )

    def add(mapping_product, ext, pd, price, src, load):
        matching_service.upsert_mapping(
            conn,
            product_id=mapping_product.id,
            external_key=ext,
            external_name=ext,
            method="exact",
            similarity=100.0,
            status="confirmed",
        )
        conn.execute(
            """
            INSERT INTO prices (product_id, source_id, load_id, price, price_date, external_key)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (mapping_product.id, src.id, load, price, pd, ext),
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

    add(p1, "EXT-A1", "2026-09-01", 70.0, sup1, load1)
    add(p1, "EXT-A1", "2026-09-10", 75.0, sup1, load1)
    add(p1, "EXT-A2", "2026-09-10", 72.0, sup2, load2)
    add(p2, "EXT-B1", "2026-09-05", 100.0, sup1, load1)
    conn.commit()
    return p1


def test_latest_prices(conn):
    _seed(conn)
    rows = supplier_service.latest_prices(conn)
    by_key = {(r["sku"], r["source_name"]): r for r in rows}
    assert len(rows) == 3
    assert ("SKU-A", "С1") in by_key
    assert ("SKU-A", "С2") in by_key
    assert ("SKU-B", "С1") in by_key
    assert by_key[("SKU-A", "С1")]["price"] == 75.0
    assert by_key[("SKU-A", "С1")]["price_date"] == "2026-09-10"


def test_latest_prices_period(conn):
    p = _seed(conn)
    rows = supplier_service.latest_prices(conn, date_from="2026-09-11")
    assert rows == []
    rows = supplier_service.latest_prices(conn, date_to="2026-09-02")
    assert len(rows) == 1
    assert rows[0]["sku"] == "SKU-A"
    assert rows[0]["source_name"] == "С1"
    assert rows[0]["price"] == 70.0


def test_latest_prices_category(conn):
    _seed(conn)
    rows = supplier_service.latest_prices(conn, category="Молочка")
    assert len(rows) == 3
    rows = supplier_service.latest_prices(conn, category="Чай")
    assert rows == []


def test_product_summary(conn):
    p = _seed(conn)
    rows = supplier_service.product_summary(conn, p.id)
    by_name = {r["source_name"]: r for r in rows}
    s1 = by_name["С1"]
    assert s1["last_price"] == pytest.approx(75.0)
    assert s1["min_price"] == pytest.approx(70.0)
    assert s1["max_price"] == pytest.approx(75.0)
    assert s1["avg_price"] == pytest.approx(72.5)
    assert s1["first_date"] == "2026-09-01"
    assert s1["last_date"] == "2026-09-10"


def test_product_summary_period(conn):
    p = _seed(conn)
    rows = supplier_service.product_summary(conn, p.id, date_from="2026-09-09")
    by_name = {r["source_name"]: r for r in rows}
    assert by_name["С1"]["last_price"] == pytest.approx(75.0)
    assert by_name["С1"]["min_price"] == pytest.approx(75.0)
    conn2 = db_module.init_db()
    assert True


def test_cheapest(conn):
    p = _seed(conn)
    stats = supplier_service.stats_for_product(conn, p.id)
    assert len(stats) == 2
    cheapest = core_suppliers.pick_cheapest(stats)
    assert cheapest.source_name == "С2"
    pct = core_suppliers.diff_percent(80.0, cheapest.last_price)
    assert pct == pytest.approx(-10.0)


def test_diff_percent():
    assert core_suppliers.diff_percent(100.0, 90.0) == pytest.approx(-10.0)
    assert core_suppliers.diff_percent(100.0, 110.0) == pytest.approx(10.0)
    assert core_suppliers.diff_percent(0, 90.0) is None


def test_list_products_with_supplier_prices(conn):
    _seed(conn)
    products = supplier_service.list_products_with_supplier_prices(conn)
    assert {p["sku"] for p in products} == {"SKU-A", "SKU-B"}
    products = supplier_service.list_products_with_supplier_prices(conn, category="Молочка")
    assert len(products) == 2
    products = supplier_service.list_products_with_supplier_prices(conn, category="Чай")
    assert products == []


def test_list_supplier_categories(conn):
    _seed(conn)
    assert supplier_service.list_supplier_categories(conn) == ["Молочка"]


def test_list_supplier_sources(conn):
    _seed(conn)
    sources = supplier_service.list_supplier_sources(conn)
    assert [s["name"] for s in sources] == ["С1", "С2"]

    # источники без цен не попадают
    from app.services import sources as sources_service

    sup3 = sources_service.create_source(
        conn, name="С3", scope="supplier", kind="csv_upload",
        origin_url="https://sup3.example", terms_agreed=True,
    )
    sources = supplier_service.list_supplier_sources(conn)
    assert [s["name"] for s in sources] == ["С1", "С2"]


def test_average():
    assert core_suppliers.compute_avg([10, 20]) == 15.0
    assert core_suppliers.compute_avg([]) == 0.0


def test_product_series(conn):
    p = _seed(conn)
    series = supplier_service.product_series(conn, p.id)
    assert len(series) == 3
    assert all("source_name" in s for s in series)
    dates = [s["price_date"] for s in series]
    assert dates == sorted(dates)

def _raw_price(conn, product, ext, price, price_date, load_id):
    """Цена из разового файла: без source_id, контур берётся из loads.scope."""
    from app.services import matching as matching_service

    matching_service.upsert_mapping(
        conn,
        product_id=product.id,
        external_key=ext,
        external_name=ext,
        method="exact",
        similarity=100.0,
        status="confirmed",
    )
    conn.execute(
        """
        INSERT INTO prices (product_id, source_id, load_id, price, price_date, external_key)
        VALUES (?, NULL, ?, ?, ?, ?)
        """,
        (product.id, load_id, price, price_date, ext),
    )


def test_raw_file_supplier_prices_visible(conn):
    """Прайс, загруженный разовым файлом (без регистрации источника), виден."""
    from app.services import catalog as catalog_service

    p = catalog_service.create_product(
        conn, "SKU-RAW", "Кефир 1л", 90.0, category="Молочка"
    )
    load_id = conn.execute(
        "INSERT INTO loads (source_id, scope, kind, status) "
        "VALUES (NULL, 'supplier', 'csv_upload', 'success')"
    ).lastrowid
    _raw_price(conn, p, "RAW-1", 55.0, "2026-09-10", load_id)
    conn.commit()

    rows = supplier_service.latest_prices(conn)
    assert [(r["sku"], r["price"], r["source_name"]) for r in rows] == [
        ("SKU-RAW", 55.0, "Разовый файл")
    ]
    assert supplier_service.has_raw_supplier_prices(conn) is True
    assert supplier_service.list_supplier_categories(conn) == ["Молочка"]
    assert [p["sku"] for p in supplier_service.list_products_with_supplier_prices(conn)] == [
        "SKU-RAW"
    ]

    only_raw = supplier_service.latest_prices(
        conn, source_id=supplier_service.RAW_SOURCE_ID
    )
    assert len(only_raw) == 1


def test_raw_competitor_prices_not_mixed_into_suppliers(conn):
    """Разовые цены конкурентов и поставщиков не путаются: у обеих source_id IS NULL."""
    from app.services import catalog as catalog_service

    p = catalog_service.create_product(
        conn, "SKU-MIX", "Творог 5%", 100.0, category="Молочка"
    )
    sup_load = conn.execute(
        "INSERT INTO loads (source_id, scope, kind, status) "
        "VALUES (NULL, 'supplier', 'csv_upload', 'success')"
    ).lastrowid
    comp_load = conn.execute(
        "INSERT INTO loads (source_id, scope, kind, status) "
        "VALUES (NULL, 'competitor', 'csv_upload', 'success')"
    ).lastrowid
    _raw_price(conn, p, "MIX-S", 40.0, "2026-09-01", sup_load)
    # цена конкурента позже — не должна вытеснить прайс поставщика
    _raw_price(conn, p, "MIX-C", 95.0, "2026-09-20", comp_load)
    conn.commit()

    rows = supplier_service.latest_prices(conn)
    assert [(r["price"], r["source_name"]) for r in rows] == [(40.0, "Разовый файл")]

    stats = supplier_service.stats_for_product(conn, p.id)
    assert [(s.source_name, s.last_price, s.max_price) for s in stats] == [
        ("Разовый файл", 40.0, 40.0)
    ]
    assert [r["price"] for r in supplier_service.product_series(conn, p.id)] == [40.0]


def test_supplier_prices_with_period_filter(conn):
    """Фильтр по периоду не должен сдвигать привязку параметров запроса."""
    _seed(conn)

    all_rows = supplier_service.latest_prices(conn)
    wide = supplier_service.latest_prices(
        conn, date_from="2000-01-01", date_to="2099-12-31"
    )
    assert [(r["sku"], r["price"]) for r in wide] == [
        (r["sku"], r["price"]) for r in all_rows
    ]
