import pytest

from app.core import report as core_report
from app.services import catalog as catalog_service
from app.services import matching as matching_service
from app.services import report as report_service
from app.services import sources as sources_service
from app.storage import db as db_module


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    monkeypatch.setattr(db_module, "_db_path", lambda: tmp_path / "test.db")
    connection = db_module.init_db()
    yield connection
    connection.close()


def _src(conn, name="К1", scope="competitor"):
    src = sources_service.create_source(
        conn,
        name=name,
        scope=scope,
        kind="csv_upload",
        origin_url="https://k.example",
        terms_agreed=True,
    )
    return src


def _load(conn, src, scope="competitor"):
    return conn.execute(
        "INSERT INTO loads (source_id, scope, kind, status) VALUES (?, ?, 'csv_upload', 'success')",
        (src.id, scope),
    ).lastrowid


def _add_price(conn, product_id, src, external_key, price, price_date, load_id):
    conn.execute(
        """
        INSERT INTO prices (product_id, source_id, load_id, price, price_date, external_key)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (product_id, src.id if src else None, load_id, price, price_date, external_key),
    )


def _confirmed_mapping(conn, product_id, external_key, method="exact", similarity=100.0):
    matching_service.upsert_mapping(
        conn,
        product_id=product_id,
        external_key=external_key,
        external_name="T" + external_key,
        method=method,
        similarity=similarity,
        status="confirmed",
        price=10.0,
        price_date="2026-09-01",
    )
    conn.execute(
        "UPDATE mappings SET status='confirmed' WHERE external_key=? AND product_id=?",
        (external_key, product_id),
    )
    conn.commit()


def test_compute_deviation():
    assert core_report.compute_deviation(100, 90) == pytest.approx(-10.0)
    assert core_report.compute_deviation(100, 110) == pytest.approx(10.0)
    assert core_report.compute_deviation(0, 50) is None
    assert core_report.compute_deviation(None, 50) is None


def test_is_over_threshold():
    assert core_report.is_over_threshold(-6.0, 5.0)
    assert core_report.is_over_threshold(-5.0, 5.0)
    assert not core_report.is_over_threshold(-4.9, 5.0)
    assert not core_report.is_over_threshold(10.0, 5.0)
    assert not core_report.is_over_threshold(None, 5.0)


def test_threshold_filter_in_sql(conn):
    p = catalog_service.create_product(conn, "SKU-1", "Товар 1", 100.0, category="Кофе")
    src = _src(conn)
    load_id = _load(conn, src)
    _confirmed_mapping(conn, p.id, "SKU-1")
    own = p.own_price

    _add_price(conn, p.id, src, "SKU-1", own * 0.9, "2026-09-10", load_id)
    conn.commit()

    rows = report_service.list_deviations(conn, threshold=5.0)
    assert len(rows) == 1
    assert rows[0]["deviation_pct"] == pytest.approx(-10.0)
    assert rows[0]["source_name"] == "К1"
    assert rows[0]["price_date"] == "2026-09-10"

    rows = report_service.list_deviations(conn, threshold=15.0)
    assert len(rows) == 0


def test_only_shortfall_rows_included(conn):
    p = catalog_service.create_product(conn, "SKU-2", "Товар 2", 100.0, category="Кофе")
    src = _src(conn)
    load_id = _load(conn, src)
    _confirmed_mapping(conn, p.id, "SKU-2")

    _add_price(conn, p.id, src, "SKU-2", p.own_price * 1.1, "2026-09-10", load_id)
    conn.commit()

    rows = report_service.list_deviations(conn, threshold=5.0)
    assert len(rows) == 0


def test_only_confirmed_or_auto_mappings(conn):
    p = catalog_service.create_product(conn, "SKU-3", "Товар 3", 100.0, category="Чай")
    src = _src(conn)
    load_id = _load(conn, src)
    _confirmed_mapping(conn, p.id, "SKU-3")

    p2 = catalog_service.create_product(conn, "SKU-4", "Товар 4", 100.0, category="Чай")
    matching_service.upsert_mapping(
        conn,
        product_id=p2.id,
        external_key="SKU-4",
        external_name="T",
        method="fuzzy",
        similarity=80.0,
        status="pending",
    )
    conn.commit()

    _add_price(conn, p.id, src, "SKU-3", p.own_price * 0.7, "2026-09-10", load_id)
    _add_price(conn, p2.id, src, "SKU-4", p2.own_price * 0.7, "2026-09-10", load_id)
    conn.commit()

    rows = report_service.list_deviations(conn, threshold=5.0)
    assert len(rows) == 1
    assert rows[0]["sku"] == "SKU-3"


def test_rejected_not_included(conn):
    p = catalog_service.create_product(conn, "SKU-5", "Товар 5", 100.0, category="Чай")
    src = _src(conn)
    load_id = _load(conn, src)
    _confirmed_mapping(conn, p.id, "SKU-5")

    p2 = catalog_service.create_product(conn, "SKU-6", "Товар 6", 100.0, category="Чай")
    matching_service.upsert_mapping(
        conn,
        product_id=p2.id,
        external_key="SKU-6",
        external_name="T",
        method="fuzzy",
        similarity=80.0,
        status="rejected",
    )
    conn.commit()

    _add_price(conn, p.id, src, "SKU-5", p.own_price * 0.7, "2026-09-10", load_id)
    _add_price(conn, p2.id, src, "SKU-6", p2.own_price * 0.7, "2026-09-10", load_id)
    conn.commit()

    rows = report_service.list_deviations(conn, threshold=5.0)
    assert len(rows) == 1


def test_supplier_scope_excluded(conn):
    p = catalog_service.create_product(conn, "SKU-7", "Товар 7", 100.0, category="Чай")
    src = _src(conn, name="П1", scope="supplier")
    load_id = _load(conn, src, scope="supplier")
    _confirmed_mapping(conn, p.id, "SKU-7")

    _add_price(conn, p.id, src, "SKU-7", p.own_price * 0.7, "2026-09-10", load_id)
    conn.commit()

    rows = report_service.list_deviations(conn, threshold=5.0)
    assert len(rows) == 0


def test_raw_file_competitor_included(conn):
    p = catalog_service.create_product(conn, "SKU-7R", "Товар 7R", 100.0, category="Чай")
    load_id = conn.execute(
        "INSERT INTO loads (source_id, scope, kind, status) VALUES (NULL, 'competitor', 'csv_upload', 'success')"
    ).lastrowid
    _confirmed_mapping(conn, p.id, "SKU-7R")
    _add_price(conn, p.id, None, "SKU-7R", p.own_price * 0.7, "2026-09-10", load_id)
    conn.commit()

    rows = report_service.list_deviations(conn, threshold=5.0)
    assert len(rows) == 1
    assert rows[0]["source_name"] == "Разовый файл"
    assert rows[0]["source_id"] is None

    rows = report_service.list_deviations(conn, threshold=5.0, source_id=report_service.RAW_SOURCE_ID)
    assert len(rows) == 1

    total = report_service.total_deviations(conn)
    assert total == 1


def test_raw_file_supplier_excluded(conn):
    p = catalog_service.create_product(conn, "SKU-7S", "Товар 7S", 100.0, category="Чай")
    load_id = conn.execute(
        "INSERT INTO loads (source_id, scope, kind, status) VALUES (NULL, 'supplier', 'csv_upload', 'success')"
    ).lastrowid
    _confirmed_mapping(conn, p.id, "SKU-7S")
    _add_price(conn, p.id, None, "SKU-7S", p.own_price * 0.7, "2026-09-10", load_id)
    conn.commit()

    rows = report_service.list_deviations(conn, threshold=5.0)
    assert rows == []
    assert report_service.total_deviations(conn) == 0


def test_filter_category_source_period(conn):
    p1 = catalog_service.create_product(conn, "SKU-8", "Товар 8", 100.0, category="Кофе")
    p2 = catalog_service.create_product(conn, "SKU-9", "Товар 9", 100.0, category="Чай")
    src = _src(conn)
    src2 = _src(conn, name="К2")
    load_id = _load(conn, src)
    load_id2 = _load(conn, src2)

    for p, s, lid in ((p1, src, load_id), (p2, src2, load_id2)):
        _confirmed_mapping(conn, p.id, p.sku)
        _add_price(conn, p.id, s, p.sku, p.own_price * 0.5, "2026-09-10", lid)
    conn.commit()

    rows = report_service.list_deviations(conn, threshold=5.0, category="Кофе")
    assert [r["sku"] for r in rows] == ["SKU-8"]

    rows = report_service.list_deviations(conn, threshold=5.0, source_id=src2.id)
    assert [r["sku"] for r in rows] == ["SKU-9"]

    rows = report_service.list_deviations(conn, threshold=5.0, date_from="2026-09-11")
    assert rows == []


def test_latest_price_used(conn):
    p = catalog_service.create_product(conn, "SKU-10", "Товар 10", 100.0, category="Кофе")
    src = _src(conn)
    load_id = _load(conn, src)
    _confirmed_mapping(conn, p.id, "SKU-10")

    _add_price(conn, p.id, src, "SKU-10", 1.0, "2026-09-01", load_id)
    _add_price(conn, p.id, src, "SKU-10", p.own_price * 0.6, "2026-09-15", load_id)
    conn.commit()

    rows = report_service.list_deviations(conn, threshold=5.0)
    assert len(rows) == 1
    assert rows[0]["price_date"] == "2026-09-15"
    assert rows[0]["deviation_pct"] == pytest.approx(-40.0)


def test_total_deviations_count(conn):
    p1 = catalog_service.create_product(conn, "SKU-11", "Товар 11", 100.0, category="Чай")
    p2 = catalog_service.create_product(conn, "SKU-12", "Товар 12", 100.0, category="Чай")
    src = _src(conn)
    load_id = _load(conn, src)
    for p in (p1, p2):
        _confirmed_mapping(conn, p.id, p.sku)
        _add_price(conn, p.id, src, p.sku, p.own_price * 0.8, "2026-09-10", load_id)
    conn.commit()

    total = report_service.total_deviations(conn)
    assert total == 2
    total = report_service.total_deviations(conn, category="Кофе")
    assert total == 0


def test_sort_by_deviation():
    rows = [
        core_report.Deviation(
            product_id=1, sku="A", product_name="A", category="Чай",
            own_price=100.0, competitor_price=95.0, deviation_pct=-5.0,
            delta_rub=-5.0, source_name="К1", source_id=1, price_date="2026-09-01",
        ),
        core_report.Deviation(
            product_id=2, sku="B", product_name="B", category="Чай",
            own_price=100.0, competitor_price=80.0, deviation_pct=-20.0,
            delta_rub=-20.0, source_name="К1", source_id=1, price_date="2026-09-01",
        ),
    ]
    rows = core_report.sort_by_deviation(rows)
    assert [r.sku for r in rows] == ["B", "A"]


def test_zero_own_price_excluded(conn):
    p = catalog_service.create_product(conn, "SKU-13", "Товар 13", 0, category="Чай")
    src = _src(conn)
    load_id = _load(conn, src)
    _confirmed_mapping(conn, p.id, "SKU-13")
    _add_price(conn, p.id, src, "SKU-13", 10.0, "2026-09-10", load_id)
    conn.commit()

    rows = report_service.list_deviations(conn, threshold=5.0)
    assert len(rows) == 0

def test_period_filter_keeps_rows_in_range(conn):
    """Широкий период не должен обнулять отчёт.

    Плейсхолдеры SQLite связываются по позиции в тексте запроса: подзапрос
    «последняя цена» стоит раньше внешнего WHERE, поэтому его параметры должны
    передаваться первыми. При перепутанном порядке порог попадал в сравнение
    дат и отчёт всегда выходил пустым.
    """
    p = catalog_service.create_product(
        conn, "SKU-PD", "Товар PD", 100.0, category="Чай"
    )
    src = _src(conn, "К-PD")
    load_id = _load(conn, src)
    _confirmed_mapping(conn, p.id, "SKU-PD")
    _add_price(conn, p.id, src, "SKU-PD", 70.0, "2026-09-10", load_id)
    conn.commit()

    unfiltered = report_service.list_deviations(conn, threshold=5.0)
    assert len(unfiltered) == 1

    wide = report_service.list_deviations(
        conn, threshold=5.0, date_from="2000-01-01", date_to="2099-12-31"
    )
    assert [r["sku"] for r in wide] == ["SKU-PD"]

    outside = report_service.list_deviations(
        conn, threshold=5.0, date_from="2026-10-01", date_to="2026-10-31"
    )
    assert outside == []


def test_raw_supplier_price_does_not_displace_competitor(conn):
    """Разовая цена поставщика не должна вытеснять разовую цену конкурента."""
    p = catalog_service.create_product(
        conn, "SKU-MIXC", "Товар MIXC", 100.0, category="Чай"
    )
    comp_load = conn.execute(
        "INSERT INTO loads (source_id, scope, kind, status) "
        "VALUES (NULL, 'competitor', 'csv_upload', 'success')"
    ).lastrowid
    sup_load = conn.execute(
        "INSERT INTO loads (source_id, scope, kind, status) "
        "VALUES (NULL, 'supplier', 'csv_upload', 'success')"
    ).lastrowid
    _confirmed_mapping(conn, p.id, "MIXC-C")
    _confirmed_mapping(conn, p.id, "MIXC-S")
    _add_price(conn, p.id, None, "MIXC-C", 80.0, "2026-09-01", comp_load)
    # прайс поставщика позже по дате, но в отчёт конкурентов попадать не должен
    _add_price(conn, p.id, None, "MIXC-S", 40.0, "2026-09-20", sup_load)

    conn.commit()

    rows = report_service.list_deviations(conn, threshold=5.0)
    assert [(r["sku"], r["price"]) for r in rows] == [("SKU-MIXC", 80.0)]
