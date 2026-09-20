import io

import pandas as pd
import pytest

from app.services import catalog as catalog_service
from app.services import loads as loads_service
from app.services import sources as sources_service
from app.services.loads import LoadError
from app.storage import db as db_module


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    monkeypatch.setattr(db_module, "_db_path", lambda: tmp_path / "test.db")
    connection = db_module.init_db()
    yield connection
    connection.close()


def _csv_bytes(rows: list[list[str]], sep: str = ",") -> bytes:
    import csv

    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=sep)
    writer.writerows(rows)
    return buf.getvalue().encode("utf-8")


def _make_competitor(conn) -> sources_service.Source:
    return sources_service.create_source(
        conn,
        name="К1",
        scope="competitor",
        kind="csv_upload",
        origin_url="https://k1.example",
        terms_agreed=True,
    )


def test_csv_with_exact_sku_imports(conn):
    catalog_service.create_product(conn, "COF-1", "Кофе Арабика 250г", 300.0)
    src = _make_competitor(conn)
    data = _csv_bytes(
        [
            ["sku", "название", "цена", "дата"],
            ["cof-1", "Кофе Арабика 250г", "299,90", "2026-09-01"],
        ]
    )
    result = loads_service.run_import(conn, data, "k1.csv", "competitor", src.id)
    assert result.ok_rows == 1
    assert result.total_rows == 1
    assert result.status == "success"

    price = conn.execute("SELECT * FROM prices").fetchone()
    assert price["price"] == 299.9
    assert price["product_id"] == conn.execute(
        "SELECT id FROM products WHERE sku='COF-1'"
    ).fetchone()["id"]


def test_repeated_upload_is_idempotent(conn):
    catalog_service.create_product(conn, "COF-1", "Кофе Арабика 250г", 300.0)
    src = _make_competitor(conn)
    data = _csv_bytes(
        [["sku", "цена"], ["COF-1", "299.90"]]
    )
    first = loads_service.run_import(conn, data, "k1.csv", "competitor", src.id)
    second = loads_service.run_import(conn, data, "k1.csv", "competitor", src.id)
    assert first.ok_rows == 1
    assert second.ok_rows == 0
    assert second.duplicate_rows == 1
    assert conn.execute("SELECT COUNT(*) c FROM prices").fetchone()["c"] == 1


def test_pending_fuzzy_row_not_imported(conn):
    catalog_service.create_product(conn, "TEA", "Чай зелёный листовой", 150.0)
    src = _make_competitor(conn)
    data = _csv_bytes(
        [
            ["sku", "название", "цена", "дата"],
            ["", "Чай листовой новый сорт 250г", "999", "2026-09-01"],
        ]
    )
    result = loads_service.run_import(conn, data, "k1.csv", "competitor", src.id)
    assert result.ok_rows == 0
    assert result.pending_rows == 1
    assert conn.execute("SELECT COUNT(*) c FROM prices").fetchone()["c"] == 0
    m = conn.execute("SELECT * FROM mappings").fetchone()
    assert m["status"] == "pending"
    assert m["price"] == 999.0


def test_unmatched_row_reported(conn):
    catalog_service.create_product(conn, "COF-1", "Кофе Арабика 250г", 300.0)
    src = _make_competitor(conn)
    data = _csv_bytes(
        [
            ["sku", "название", "цена"],
            ["", "Совершенно другой товар", "100"],
        ]
    )
    result = loads_service.run_import(conn, data, "k1.csv", "competitor", src.id)
    assert result.error_rows == 1
    assert result.ok_rows == 0
    assert any("не сопоставлен" in e for e in result.errors)


def test_currency_distinct_from_rub_rejected(conn):
    catalog_service.create_product(conn, "COF-1", "Кофе Арабика", 300.0)
    src = _make_competitor(conn)
    data = _csv_bytes(
        [
            ["sku", "цена", "валюта"],
            ["COF-1", "5", "$"],
            ["COF-1", "300", "RUB"],
        ]
    )
    result = loads_service.run_import(conn, data, "k1.csv", "competitor", src.id)
    assert result.error_rows == 1
    assert result.ok_rows == 1
    assert any("рубли" in e for e in result.errors)


def test_unapproved_source_blocked(conn):
    catalog_service.create_product(conn, "A", "Товар", 10.0)
    src = sources_service.create_source(
        conn, name="Заблокированный", scope="competitor",
        kind="csv_upload", origin_url="https://x", terms_agreed=False,
    )
    data = _csv_bytes([["sku", "цена"], ["A", "1"]])
    with pytest.raises(LoadError, match="не разрешён"):
        loads_service.run_import(conn, data, "x.csv", "competitor", src.id)


def test_wrong_extension_rejected(conn):
    data = "sku,цена\nA,1".encode("utf-8")
    with pytest.raises(LoadError, match="file.txt"):
        loads_service.run_import(conn, data, "file.txt", "competitor", None)


def test_missing_price_column_rejected(conn):
    data = _csv_bytes([["sku", "дата"], ["A", "2026-09-01"]])
    with pytest.raises(LoadError, match="колонка"):
        loads_service.run_import(conn, data, "x.csv", "competitor", None)


def test_rejected_pair_skipped(conn):
    product = catalog_service.create_product(conn, "COF-1", "Кофе Арабика 250г", 300.0)
    src = _make_competitor(conn)
    import app.core.match as mc

    matching_service = __import__("app.services.matching", fromlist=["upsert_mapping"])
    matching_service.upsert_mapping(
        conn,
        product_id=product.id,
        external_key="",
        external_name="Кофе Арабика 250г",
        method=mc.METHOD_MANUAL,
        similarity=95.0,
        status=mc.STATUS_REJECTED,
    )
    data = _csv_bytes(
        [["sku", "название", "цена"], ["", "Кофе Арабика 250г", "200"]]
    )
    result = loads_service.run_import(conn, data, "k1.csv", "competitor", src.id)
    assert result.error_rows == 1
    assert result.ok_rows == 0
    assert conn.execute("SELECT COUNT(*) c FROM prices").fetchone()["c"] == 0


def test_one_time_upload_without_source(conn):
    catalog_service.create_product(conn, "COF-1", "Кофе Арабика 250г", 300.0)
    data = _csv_bytes([["sku", "цена"], ["COF-1", "10"]])
    result = loads_service.run_import(conn, data, "razovyi.csv", "supplier", None)
    assert result.ok_rows == 1
    price = conn.execute("SELECT * FROM prices").fetchone()
    assert price["source_id"] is None


def test_load_records_persisted(conn):
    catalog_service.create_product(conn, "COF-1", "Кофе Арабика 250г", 300.0)
    src = _make_competitor(conn)
    data = _csv_bytes(
        [["sku", "цена"], ["COF-1", "10"], ["", "5"]]
    )
    result = loads_service.run_import(conn, data, "k1.csv", "competitor", src.id)
    assert result.status == "partial"
    load = loads_service.get_load(conn, result.load_id)
    assert load["status"] == "partial"
    assert load["ok_rows"] == 1
    assert load["error_rows"] == 1
    assert load["errors_summary"] != "[]"
    load = loads_service.get_load(conn, result.load_id)
    assert load["status"] == "partial"
    assert load["ok_rows"] == 1
    assert load["error_rows"] == 1
    assert load["errors_summary"] != "[]"