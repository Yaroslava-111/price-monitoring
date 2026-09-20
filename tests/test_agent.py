import pytest

from app.services import agent as agent_service
from app.services import catalog as catalog_service
from app.services import matching as matching_service
from app.services import sources as sources_service
from app.storage import db as db_module


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    monkeypatch.setattr(db_module, "_db_path", lambda: tmp_path / "test.db")
    connection = db_module.init_db()
    yield connection
    connection.close()


def _seed(conn):
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

    prices = (
        (comp, load, 170.0, "2026-09-01"),  # дешевле нашей цены 200 → рекомендация
        (sup, load_s, 150.0, "2026-09-02"),
    )
    for src, lid, price, d in prices:
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
    return p, comp, sup, load, load_s


def _make_pending(conn, p, comp):
    matching_service.upsert_mapping(
        conn,
        product_id=p.id,
        external_key="PEND-1",
        external_name="Кофе зерновой 250 г",
        method="fuzzy",
        similarity=80.0,
        status="pending",
    )
    conn.commit()
    row = matching_service.list_pending(conn)[0]
    return row["id"]


def test_agent_defaults(conn):
    adapter = agent_service.load_agent(conn)
    assert adapter.endpoint == ""
    assert adapter.model == ""


def _log_rows(conn):
    return conn.execute("SELECT * FROM agent_log ORDER BY id").fetchall()


def test_agent_logs_match_suggest(conn):
    p, *_ = _seed(conn)
    mid = _make_pending(conn, p, None)
    adapter = agent_service.AgentAdapter(conn)
    result = adapter.match_suggest(mid)
    assert result["mapping_id"] == mid
    assert 0 < result["confidence"] <= 100
    assert "рациональ" in result["rationale"] or "Рекоменд" in result["rationale"]

    records = _log_rows(conn)
    assert records and records[-1]["task"] == "match_suggest"
    assert records[-1]["status"] == "success"
    assert "Кофе" in records[-1]["result"]

    row = conn.execute(
        "SELECT ai_recommended FROM mappings WHERE id = ?", (mid,)
    ).fetchone()
    assert row["ai_recommended"] == 1
    rec = conn.execute("SELECT * FROM recommendations").fetchone()
    assert rec["rec_type"] == "match"


def test_agent_match_suggest_skips_rejected(conn):
    p, *_ = _seed(conn)
    mid = _make_pending(conn, p, None)
    matching_service.upsert_mapping(
        conn,
        product_id=p.id,
        external_key="PEND-1",
        external_name="Название откл",
        method="manual",
        similarity=80.0,
        status="rejected",
    )
    conn.commit()
    adapter = agent_service.AgentAdapter(conn)
    with pytest.raises(agent_service.AgentError):
        adapter.match_suggest(mid)


def test_agent_analyze_competitors(conn):
    p, *_ = _seed(conn)
    adapter = agent_service.AgentAdapter(conn)
    recs = adapter.analyze_prices("competitor")
    assert len(recs) == 1
    assert recs[0]["rec_type"] == "price_change"
    assert recs[0]["target_price"] == 170.0

    db_rec = conn.execute(
        "SELECT * FROM recommendations WHERE rec_type = 'price_change'"
    ).fetchone()
    assert db_rec["status"] == "proposed"
    assert "Конкурент" in db_rec["payload"]

    records = _log_rows(conn)
    assert records[-1]["task"] == "analyze_prices"
    assert records[-1]["status"] == "success"


def test_agent_analyze_suppliers(conn):
    p, *_ = _seed(conn)
    adapter = agent_service.AgentAdapter(conn)
    recs = adapter.analyze_prices("supplier")
    assert len(recs) == 1
    assert recs[0]["rec_type"] == "source_switch"
    assert recs[0]["supplier"] == "С9"
    assert recs[0]["supplier_price"] == 150.0


def test_agent_analyze_dedup(conn):
    p, *_ = _seed(conn)
    adapter = agent_service.AgentAdapter(conn)
    assert len(adapter.analyze_prices("competitor")) == 1
    assert len(adapter.analyze_prices("competitor")) == 0
    count = conn.execute(
        "SELECT COUNT(*) FROM recommendations WHERE rec_type = 'price_change'"
    ).fetchone()[0]
    assert count == 1


def test_agent_analyze_invalid_scope(conn):
    adapter = agent_service.AgentAdapter(conn)
    with pytest.raises(agent_service.AgentError):
        adapter.analyze_prices("bogus")


def test_latest_match_rationale(conn):
    p, *_ = _seed(conn)
    mid = _make_pending(conn, p, None)
    adapter = agent_service.AgentAdapter(conn)
    adapter.match_suggest(mid)
    rationale = agent_service.latest_match_rationale(conn, mid)
    assert rationale

    assert agent_service.latest_match_rationale(conn, 999999) == ""