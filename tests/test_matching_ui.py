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


def _seed_two_mappings(conn):
    from app.services import catalog as catalog_service
    from app.services import matching as matching_service
    from app.services import sources as sources_service

    p1 = catalog_service.create_product(conn, "SKU1", "Кофе Арабика 250г", 300.0)
    p2 = catalog_service.create_product(conn, "SKU2", "Чай зелёный 100г", 150.0)
    src1 = sources_service.create_source(
        conn, name="К1", scope="competitor", kind="csv_upload",
        origin_url="https://k1.example", terms_agreed=True,
    )
    src2 = sources_service.create_source(
        conn, name="К2", scope="competitor", kind="csv_upload",
        origin_url="https://k2.example", terms_agreed=True,
    )
    load1 = conn.execute(
        "INSERT INTO loads (source_id, scope, kind, status) VALUES (?, 'competitor', 'csv_upload', 'success')",
        (src1.id,),
    ).lastrowid
    load2 = conn.execute(
        "INSERT INTO loads (source_id, scope, kind, status) VALUES (?, 'competitor', 'csv_upload', 'success')",
        (src2.id,),
    ).lastrowid
    conn.commit()

    matching_service.upsert_mapping(
        conn,
        product_id=p1.id,
        external_key="EXT-1",
        external_name="Кофе Арабика зерно 250г премиум",
        method="fuzzy",
        similarity=82.0,
        status="pending",
        price=255.5,
        price_date="2026-09-01",
        load_id=load1,
    )
    matching_service.upsert_mapping(
        conn,
        product_id=p2.id,
        external_key="EXT-2",
        external_name="Чай зелёный премиум 100г",
        method="fuzzy",
        similarity=75.0,
        status="pending",
        price=120.0,
        price_date="2026-09-02",
        load_id=load2,
    )
    return p1, p2


def test_matching_screen_empty(conn):
    at = AppTest.from_file(MAIN_SCRIPT, default_timeout=30)
    at.run()
    at.switch_page("screens/matching.py")
    at.run()
    assert not list(at.exception)


def test_matching_screen_confirm_rejects(conn):
    _seed_two_mappings(conn)
    conn.close()

    at = AppTest.from_file(MAIN_SCRIPT, default_timeout=30)
    at.run()
    at.switch_page("screens/matching.py")
    at.run()
    assert not list(at.exception)

    buttons = {b.key: b for b in at.button}
    confirm_key = next(k for k in buttons if k.endswith("_confirm"))
    reject_key = next(k for k in buttons if k.endswith("_reject"))

    buttons[confirm_key].click()
    at.run()
    assert not list(at.exception)

    conn2 = db_module.init_db()
    try:
        status1 = conn2.execute(
            "SELECT status FROM mappings WHERE external_key='EXT-1'"
        ).fetchone()["status"]
        assert status1 == "confirmed"
        price = conn2.execute(
            "SELECT * FROM prices ORDER BY id"
        ).fetchall()
        assert len(price) == 1
        assert price[0]["external_key"] == "EXT-1"
    finally:
        conn2.close()


def test_matching_reject_blacklists(conn):
    _seed_two_mappings(conn)
    conn.close()

    at = AppTest.from_file(MAIN_SCRIPT, default_timeout=30)
    at.run()
    at.switch_page("screens/matching.py")
    at.run()

    buttons = {b.key: b for b in at.button}
    reject_key = next(k for k in buttons if k.endswith("_reject"))
    buttons[reject_key].click()
    at.run()
    assert not list(at.exception)

    conn2 = db_module.init_db()
    try:
        rejected = conn2.execute(
            "SELECT external_key FROM mappings WHERE status='rejected' ORDER BY id"
        ).fetchall()
        assert len(rejected) == 1
        assert rejected[0]["external_key"] == "EXT-1"
        assert (
            conn2.execute(
                "SELECT status FROM mappings WHERE external_key='EXT-2'"
            ).fetchone()["status"]
            == "pending"
        )
    finally:
        conn2.close()


def test_matching_pagination_buttons(conn):
    _seed_two_mappings(conn)
    conn.close()

    at = AppTest.from_file(MAIN_SCRIPT, default_timeout=30)
    at.run()
    at.switch_page("screens/matching.py")
    at.run()
    assert not list(at.exception)
    keys = [b.key for b in at.button]
    assert any(k.endswith("_confirm") for k in keys)