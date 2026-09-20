import pytest

from app.services import catalog as catalog_service
from app.services import matching as matching_service
from app.services import sources as sources_service
from app.services.matching import MatchingError
from app.storage import db as db_module


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    monkeypatch.setattr(db_module, "_db_path", lambda: tmp_path / "test.db")
    connection = db_module.init_db()
    yield connection
    connection.close()


def _product(conn, sku="SKU", name="Товар тест"):
    return catalog_service.create_product(conn, sku, name, 100.0)


def _source(conn):
    return sources_service.create_source(
        conn, name="И1", scope="competitor", kind="csv_upload",
        origin_url="https://i1.example", terms_agreed=True,
    )


def _pending_mapping(conn, product, price=50.0, price_date="2026-09-01"):
    src = _source(conn)
    cur = conn.execute(
        "INSERT INTO loads (source_id, scope, kind, status, run_by) "
        "VALUES (?, 'competitor', 'csv_upload', 'success', 'manual')",
        (src.id,),
    )
    load_id = cur.lastrowid
    matching_service.upsert_mapping(
        conn,
        product_id=product.id,
        external_key="EXT-1",
        external_name="Внешний товар",
        method="fuzzy",
        similarity=80.0,
        status="pending",
        price=price,
        price_date=price_date,
        load_id=load_id,
    )
    mapping = matching_service.get_mapping(
        conn, conn.execute("SELECT id FROM mappings").fetchone()["id"]
    )
    return mapping


def test_confirm_mapping_inserts_price(conn):
    p = _product(conn)
    mapping = _pending_mapping(conn, p, price=55.5, price_date="2026-09-01")
    matching_service.confirm_mapping(conn, mapping["id"])
    m = matching_service.get_mapping(conn, mapping["id"])
    assert m["status"] == "confirmed"
    row = conn.execute("SELECT * FROM prices").fetchone()
    assert row["product_id"] == p.id
    assert row["price"] == 55.5
    assert row["price_date"] == "2026-09-01"
    assert row["external_key"] == "EXT-1"


def test_confirm_twice_does_not_duplicate_price(conn):
    p = _product(conn)
    mapping = _pending_mapping(conn, p, price=55.5, price_date="2026-09-01")
    matching_service.confirm_mapping(conn, mapping["id"])
    n1 = conn.execute("SELECT COUNT(*) c FROM prices").fetchone()["c"]
    matching_service.confirm_mapping(conn, mapping["id"])
    n2 = conn.execute("SELECT COUNT(*) c FROM prices").fetchone()["c"]
    assert n1 == 1
    assert n2 == 1


def test_confirm_with_manual_product_reassign(conn):
    p1 = _product(conn, "SKU1", "Товар один")
    p2 = _product(conn, "SKU2", "Товар два")
    mapping = _pending_mapping(conn, p1, price=42.0)
    matching_service.confirm_mapping(conn, mapping["id"], product_id=p2.id)
    m = matching_service.get_mapping(conn, mapping["id"])
    assert m["product_id"] == p2.id
    row = conn.execute("SELECT * FROM prices").fetchone()
    assert row["product_id"] == p2.id


def test_rejected_goes_to_blacklist(conn):
    p = _product(conn)
    mapping = _pending_mapping(conn, p)
    matching_service.mark_rejected(conn, mapping["id"])
    rejected = matching_service.list_rejected_pairs(conn)
    assert (p.id, "внешний товар") in rejected


def test_reject_non_pending_fails(conn):
    p = _product(conn)
    mapping = _pending_mapping(conn, p)
    matching_service.confirm_mapping(conn, mapping["id"])
    with pytest.raises(MatchingError, match="ожидающее"):
        matching_service.mark_rejected(conn, mapping["id"])


def test_rejected_pair_not_offered_again(conn):
    p = _product(conn)
    mapping = _pending_mapping(conn, p)
    matching_service.mark_rejected(conn, mapping["id"])

    from app.core import match as match_core

    products = [
        {"id": p.id, "sku": p.sku, "name": p.name, "is_active": True}
    ]
    result = match_core.match(
        external_sku="",
        external_name="Внешний товар",
        products=products,
        auto_threshold=90,
        manual_threshold=70,
        rejected_pairs=matching_service.list_rejected_pairs(conn),
    )
    assert result.matched is False


def test_count_pending(conn):
    p = _product(conn)
    _pending_mapping(conn, p)
    assert matching_service.count_pending(conn) == 1