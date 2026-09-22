from __future__ import annotations

import sqlite3

import pandas as pd
import streamlit as st

from app.services import catalog as catalog_service
from app.services.catalog import Product, ValidationError
from app.storage.db import get_connection

# Видимость таблицы/карточек управляется чистым CSS по ширине экрана:
# на телефоне строка таблицы обрезает длинные названия и уводит контент
# в горизонтальный скролл, поэтому там показываем карточки с полным текстом,
# на широких экранах — обычную таблицу. Оба блока отрисовываются, CSS
# прячет лишний без участия сервера. Правило инжектится только на этом
# экране, на другие страницы не попадает.
_CATALOG_CSS = """
<style>
@media (max-width: 767px) {
  .st-key-catalog_cards_phone { display: block !important; }
  [data-testid="stDataFrame"] { display: none !important; }
}
@media (min-width: 768px) {
  .st-key-catalog_cards_phone { display: none !important; }
}
</style>
"""


def _conn() -> sqlite3.Connection:
    return get_connection()


def _format_price(value: float) -> str:
    return f"{value:,.2f} ₽".replace(",", " ")


def render() -> None:
    st.header("Каталог товаров")
    st.caption(
        "Собственные товары и цены. SKU — артикул, по которому сопоставляются внешние данные."
    )

    with st.expander("Добавить товар", expanded=False, icon=":material/add:"):
        _form_add_product()

    with st.expander(
        "Импорт каталога из CSV", expanded=False, icon=":material/upload:"
    ):
        _form_import_csv()

    _list_products()

    with st.expander("Изменить товар", expanded=False, icon=":material/edit:"):
        _form_edit_product()


def _form_import_csv() -> None:
    uploaded = st.file_uploader(
        "Файл каталога (.csv)",
        type=["csv"],
        help="Колонки: sku (или артикул), название, цена, категория (необязательно). "
        "Повторные SKU пропускаются.",
    )
    if uploaded is None:
        st.info("Загрузите файл. Пример в sample_data/catalog_seed.csv.")
        return

    if st.button(
            "Импортировать товары",
            key="catalog_import_run",
            icon=":material/upload:",
        ):
        conn = _conn()
        try:
            result = catalog_service.load_catalog_csv(
                conn, uploaded.getvalue(), uploaded.name
            )
            if result.added:
                st.success(f"Добавлено товаров: {result.added}.")
            if result.skipped_sku_exists:
                st.caption(f"Пропущено (SKU уже в каталоге): {result.skipped_sku_exists}.")
            if result.errors:
                with st.expander(f"Ошибки ({len(result.errors)})"):
                    st.text("\n".join(result.errors))
            st.rerun()
        except catalog_service.ValidationError as exc:
            st.error(str(exc))
        finally:
            conn.close()


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
        submitted = st.form_submit_button("Добавить товар", icon=":material/add:")

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

    st.markdown(_CATALOG_CSS, unsafe_allow_html=True)
    _list_products_table(products)
    with st.container(key="catalog_cards_phone"):
        _list_products_cards(products)


def _list_products_cards(products: list[Product]) -> None:
    for p in products:
        with st.container(border=True):
            st.markdown(f"**{p.sku}** — {p.name}")
            st.caption(
                f"Категория: {p.category or '—'} · "
                f"Своя цена: {_format_price(p.own_price)} · "
                f"Активен: {'да' if p.is_active else 'нет'}"
            )


def _list_products_table(products: list[Product]) -> None:
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
        column_config={
            "SKU": st.column_config.TextColumn(width=90),
            "Название": st.column_config.TextColumn(width=240),
            "Категория": st.column_config.TextColumn(width=130),
            "Своя цена": st.column_config.TextColumn(width=110),
            "Активен": st.column_config.TextColumn(width=80),
        },
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
        save = col1.form_submit_button(
            "Сохранить изменения", icon=":material/save:"
        )
        delete = col2.form_submit_button(
            "Удалить товар", icon=":material/delete:"
        )

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