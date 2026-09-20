import pandas as pd
import pytest

from app.core import history as core_history
from app.services import history as history_service
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
    return p


def test_product_prices(conn):
    p = _seed(conn)
    rows = history_service.product_prices(conn, p.id)
    assert len(rows) == 3
    assert all("source_name" in r for r in rows)
    assert rows[0]["source_name"] == "К9"
    # сортировка по дате
    dates = [r["price_date"] for r in rows]
    assert dates == sorted(dates)


def test_product_prices_period(conn):
    p = _seed(conn)
    rows = history_service.product_prices(conn, p.id, date_from="2026-09-03")
    assert len(rows) == 1
    assert all(r["price_date"] >= "2026-09-03" for r in rows)
    rows = history_service.product_prices(conn, p.id, date_to="2026-09-01")
    assert len(rows) == 1
    assert rows[0]["source_name"] == "К9"


def test_series_frame():
    rows = [
        {"price": 10.0, "price_date": "2026-09-02", "source_name": "С9"},
        {"price": 9.0, "price_date": "2026-09-01", "source_name": "К9"},
    ]
    df = core_history.series_frame(rows)
    assert isinstance(df, pd.DataFrame)
    assert len(df) == 2
    assert pd.api.types.is_datetime64_any_dtype(df["price_date"])
    assert df.iloc[0]["source_name"] == "К9"


def test_series_frame_empty():
    df = core_history.series_frame([])
    assert df.empty


def test_build_figure():
    df = core_history.series_frame(
        [
            {"price": 10.0, "price_date": "2026-09-01", "source_name": "К9"},
            {"price": 12.0, "price_date": "2026-09-02", "source_name": "С9"},
        ]
    )
    fig = core_history.build_figure(df)
    assert len(fig.data) == 2
    assert fig.data[0].name in ("К9", "С9")
    assert fig.data[0].hovertemplate
    assert "Источник" in fig.data[0].hovertemplate


def test_build_figure_empty():
    fig = core_history.build_figure(pd.DataFrame())
    assert len(fig.data) == 0


def test_history_csv_has_attribution():
    df = core_history.series_frame(
        [
            {"price": 10.0, "price_date": "2026-09-01", "source_name": "К9"},
        ]
    )
    csv_text = core_history.history_csv(df)
    assert "Источник" in csv_text
    assert "К9" in csv_text