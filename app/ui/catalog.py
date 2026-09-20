from __future__ import annotations

import sqlite3

import pandas as pd
import streamlit as st

from app.services import catalog as catalog_service
from app.services.catalog import Product, ValidationError
from app.storage.db import get_connection


def _conn() -> sqlite3.Connection:
    return get_connection()


def _format_price(value: float) -> str:
    return f"{value:,.2f} ₽".replace(",", " ")


def render() -> None:
    st.header("Каталог товаров")
    st.caption(
        "Собственные товары и цены. SKU — артикул, по которому сопоставляются внешние данные."
    )

    with st.expander("➕ Добавить товар", expanded=False):
        _form_add_product()

    _list_products()

    with st.expander("✏️ Изменить товар", expanded=False):
        _form_edit_product()


def _form_add_product() -> None:
    with st.form("add_product_form", clear_on_submit=False):
        sku = st.text_input("SKU (артикул)", help="Обязателен, уникален, до 64 символов")
        name = st.text_input("Название", help="Обязательно, до 200 символов")
        category = st.text_input("Категория", help="Необязательно, до 100 символов")
        own_price = st.number_input(
            "Своя цена, ₽",
            min_value=0.0,
            max_value=float(catalog_service.PRICE_MAX),
            value=0.0,
            step=0.01,
            format="%.2f",
        )
        submitted = st.form_submit_button("Добавить товар")

    if submitted:
        conn = _conn()
        try:
            product = catalog_service.create_product(
                conn, sku, name, own_price, category
            )
            st.success(f"Добавлен товар: {product.sku} — {product.name}")
            st.rerun()
        except ValidationError as exc:
            st.error(str(exc))
        finally:
            conn.close()


def _list_products() -> None:
    st.subheader("Список товаров")
    conn = _conn()
    try:
        products = catalog_service.list_products(conn)
    finally:
        conn.close()

    if not products:
        st.info("Каталог пуст. Добавьте первый товар.")
        return

    rows = [
        {
            "SKU": p.sku,
            "Название": p.name,
            "Категория": p.category,
            "Своя цена": _format_price(p.own_price),
            "Активен": "да" if p.is_active else "нет",
        }
        for p in products
    ]
    st.dataframe(
        pd.DataFrame(rows),
        width="stretch",
        hide_index=True,
    )


def _form_edit_product() -> None:
    conn = _conn()
    try:
        products = catalog_service.list_products(conn)
    finally:
        conn.close()

    if not products:
        st.info("Каталог пуст — изменять пока нечего.")
        return

    by_label = {
        f"{p.sku} — {p.name}": p
        for p in products
    }
    label = st.selectbox("Выберите товар для изменения", list(by_label.keys()))
    product: Product = by_label[label]

    with st.form("edit_product_form"):
        sku_display = st.text_input("SKU", value=product.sku, disabled=True)
        name = st.text_input("Название", value=product.name)
        category = st.text_input("Категория", value=product.category)
        own_price = st.number_input(
            "Своя цена, ₽",
            min_value=0.0,
            max_value=float(catalog_service.PRICE_MAX),
            value=float(product.own_price),
            step=0.01,
            format="%.2f",
        )
        is_active = st.checkbox("Товар активен", value=product.is_active)
        col1, col2 = st.columns(2)
        save = col1.form_submit_button("Сохранить изменения")
        delete = col2.form_submit_button("Удалить товар")

    if save:
        conn = _conn()
        try:
            catalog_service.update_product(
                conn,
                product.id,
                name=name,
                own_price=own_price,
                category=category,
                is_active=is_active,
            )
            st.success("Изменения сохранены.")
            st.rerun()
        except ValidationError as exc:
            st.error(str(exc))
        finally:
            conn.close()

    if delete:
        conn = _conn()
        try:
            catalog_service.delete_product(conn, product.id)
            st.success("Товар удалён.")
            st.rerun()
        finally:
            conn.close()


if __name__ == "__main__":
    render()