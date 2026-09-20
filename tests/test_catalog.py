import os
import tempfile

import pytest

from app.services import catalog as catalog_service
from app.services.catalog import Product, ValidationError
from app.storage import db as db_module


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    monkeypatch.setattr(db_module, "_db_path", lambda: tmp_path / "test.db")
    connection = db_module.init_db()
    yield connection
    connection.close()


def test_create_product_minimal(conn):
    product = catalog_service.create_product(conn, "sku-1", "Кофе Арабика", 250.50)
    assert isinstance(product, Product)
    assert product.sku == "SKU-1"
    assert product.name == "Кофе Арабика"
    assert product.own_price == 250.50
    assert product.category == ""
    assert product.is_active is True


def test_sku_is_uppercased_and_trimmed(conn):
    product = catalog_service.create_product(conn, "  sku-42  ", "Чай", 100)
    assert product.sku == "SKU-42"


def test_duplicate_sku_rejected(conn):
    catalog_service.create_product(conn, "s1", "Первый", 100)
    with pytest.raises(ValidationError, match="уже существует"):
        catalog_service.create_product(conn, "S1", "Дубликат", 200)


def test_validate_required_fields(conn):
    with pytest.raises(ValidationError, match="SKU: значение не может быть пустым"):
        catalog_service.create_product(conn, "  ", "Имя", 100)
    with pytest.raises(ValidationError, match="Название: значение не может быть пустым"):
        catalog_service.create_product(conn, "s2", "   ", 100)


def test_price_validation(conn):
    with pytest.raises(ValidationError, match="отрицательной"):
        catalog_service.create_product(conn, "s3", "Имя", -1)
    with pytest.raises(ValidationError, match="обязательное числовое значение"):
        catalog_service.create_product(conn, "s4", "Имя", None)


def test_upper_price_bound(conn):
    with pytest.raises(ValidationError, match="превышает максимум"):
        catalog_service.create_product(
            conn, "s5", "Имя", catalog_service.PRICE_MAX + 1
        )


def test_sku_length_limit(conn):
    with pytest.raises(ValidationError, match="максимум 64"):
        catalog_service.create_product(conn, "x" * 65, "Имя", 100)


def test_category_length_limit(conn):
    with pytest.raises(ValidationError, match="Категория: превышает максимум 100"):
        catalog_service.create_product(conn, "s6", "Имя", 100, category="к" * 101)


def test_update_product(conn):
    product = catalog_service.create_product(conn, "sku-9", "Старое имя", 100)
    updated = catalog_service.update_product(
        conn,
        product.id,
        name="Новое имя",
        own_price=150.75,
        category="Зерно",
        is_active=False,
    )
    assert updated.name == "Новое имя"
    assert updated.own_price == 150.75
    assert updated.category == "Зерно"
    assert updated.is_active is False


def test_update_keeps_history_intact(conn):
    product = catalog_service.create_product(conn, "sku-10", "Товар", 100)
    conn.execute(
        """
        INSERT INTO loads (scope, kind, status)
        VALUES ('competitor', 'csv_upload', 'success')
        """
    )
    load_id = conn.execute("SELECT MAX(id) AS m FROM loads").fetchone()["m"]
    conn.execute(
        """
        INSERT INTO prices (product_id, source_id, load_id, price, price_date, external_key)
        VALUES (?, NULL, ?, 90, '2026-09-01', 'sku-10')
        """,
        (product.id, load_id),
    )
    conn.commit()

    catalog_service.update_product(conn, product.id, own_price=120)

    row = conn.execute("SELECT * FROM prices WHERE product_id = ?", (product.id,)).fetchone()
    assert row is not None
    assert row["price"] == 90


def test_delete_product(conn):
    product = catalog_service.create_product(conn, "sku-11", "К удалению", 100)
    catalog_service.delete_product(conn, product.id)
    assert catalog_service.get_product(conn, product.id) is None


def test_list_active_only(conn):
    catalog_service.create_product(conn, "a1", "Активный", 100)
    inactive = catalog_service.create_product(conn, "a2", "Неактивный", 100)
    catalog_service.update_product(conn, inactive.id, is_active=False)

    all_products = catalog_service.list_products(conn)
    active = catalog_service.list_products(conn, active_only=True)

    assert len(all_products) == 2
    assert len(active) == 1
    assert active[0].name == "Активный"


def test_validate_product_returns_error_list(conn):
    errors = catalog_service.validate_product("", "", "", None)
    assert any("SKU: значение не может быть пустым" in e for e in errors)
    assert any("Название: значение не может быть пустым" in e for e in errors)
    assert any("обязательное числовое значение" in e for e in errors)