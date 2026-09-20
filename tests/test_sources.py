import pytest

from app.services import sources as sources_service
from app.services.sources import Source, SourceError
from app.storage import db as db_module


@pytest.fixture()
def conn(tmp_path):
    db_module._db_path = lambda: tmp_path / "test.db"
    connection = db_module.init_db()
    yield connection
    connection.close()


def test_create_source_with_agreement(conn):
    source = sources_service.create_source(
        conn,
        name="Прайс Поставщик-А",
        scope="supplier",
        kind="price_feed",
        origin_url="https://supplier-a.ru/price.csv",
        terms_agreed=True,
        review_period_days=30,
    )
    assert isinstance(source, Source)
    assert source.status == "approved"
    assert source.approved_at is not None
    assert source.requires_attribution is True


def test_create_source_without_agreement_is_blocked(conn):
    source = sources_service.create_source(
        conn,
        name="Без согласия",
        scope="competitor",
        kind="api",
        origin_url="https://api.example.com",
        terms_agreed=False,
    )
    assert source.status == "blocked"
    assert source.approved_at is None


def test_name_required(conn):
    with pytest.raises(SourceError, match="Название"):
        sources_service.create_source(
            conn, name="  ", scope="competitor", kind="api",
            origin_url="https://ex.com",
        )


def test_invalid_scope(conn):
    with pytest.raises(SourceError, match="Неверный контур"):
        sources_service.create_source(
            conn, name="X", scope="bad", kind="api", origin_url="https://ex.com"
        )


def test_api_requires_valid_url(conn):
    with pytest.raises(SourceError, match="обязателен"):
        sources_service.create_source(
            conn, name="X", scope="competitor", kind="api", origin_url=""
        )
    with pytest.raises(SourceError, match="валидным"):
        sources_service.create_source(
            conn, name="X", scope="competitor", kind="api",
            origin_url="not-a-url",
        )


def test_name_length_limit(conn):
    with pytest.raises(SourceError, match="максимум 100"):
        sources_service.create_source(
            conn, name="и" * 101, scope="supplier", kind="csv_upload"
        )


def test_source_expires_after_review_period(conn):
    source = sources_service.create_source(
        conn,
        name="Срочный",
        scope="competitor",
        kind="csv_upload",
        terms_agreed=True,
        review_period_days=1,
    )
    conn.execute(
        "UPDATE sources SET approved_at = '2020-01-01T00:00:00' WHERE id = ?",
        (source.id,),
    )
    conn.commit()
    reloaded = sources_service.get_source(conn, source.id)
    assert reloaded.status == "expired"


def test_source_blocked_when_terms_disagreed(conn):
    source = sources_service.create_source(
        conn, name="X", scope="supplier", kind="csv_upload", terms_agreed=True
    )
    updated = sources_service.update_source(conn, source.id, terms_agreed=False)
    assert updated.status == "blocked"
    assert updated.approved_at is None or updated.status == "blocked"


def test_update_source_fields(conn):
    source = sources_service.create_source(
        conn, name="Старое", scope="competitor", kind="csv_upload"
    )
    updated = sources_service.update_source(
        conn,
        source.id,
        name="Новое",
        scope="supplier",
        kind="price_feed",
        origin_url="https://supplier-x.ru/feed.csv",
        min_refresh_minutes=60,
        requires_attribution=False,
    )
    assert updated.name == "Новое"
    assert updated.scope == "supplier"
    assert updated.kind == "price_feed"
    assert updated.min_refresh_minutes == 60
    assert updated.requires_attribution is False


def test_is_approved(conn):
    blocked = sources_service.create_source(
        conn, name="Блок", scope="competitor", kind="csv_upload"
    )
    approved = sources_service.create_source(
        conn, name="Ок", scope="competitor", kind="csv_upload", terms_agreed=True
    )
    assert sources_service.is_approved(conn, approved.id) is True
    assert sources_service.is_approved(conn, blocked.id) is False


def test_list_sources_scope_filter(conn):
    sources_service.create_source(
        conn, name="Конкурент", scope="competitor", kind="csv_upload"
    )
    sources_service.create_source(
        conn, name="Поставщик", scope="supplier", kind="csv_upload"
    )
    competitors = sources_service.list_sources(conn, scope="competitor")
    suppliers = sources_service.list_sources(conn, scope="supplier")
    assert len(competitors) == 1
    assert len(suppliers) == 1


def test_delete_source(conn):
    source = sources_service.create_source(
        conn, name="Удалить", scope="competitor", kind="csv_upload"
    )
    sources_service.delete_source(conn, source.id)
    assert sources_service.get_source(conn, source.id) is None