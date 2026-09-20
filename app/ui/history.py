from __future__ import annotations

import sqlite3

import pandas as pd
import streamlit as st

from app.core.history import build_figure, history_csv, series_frame
from app.services import catalog as catalog_service
from app.services import history as history_service
from app.storage.db import get_connection


def _conn() -> sqlite3.Connection:
    return get_connection()


def render() -> None:
    st.header("История цены товара")
    st.caption(
        "График цены по датам с атрибуцией источника. "
        "Точки окрашены по источнику, ховер показывает дату, цену и источник."
    )

    conn = _conn()
    try:
        products = catalog_service.list_products(conn, active_only=True)
        if not products:
            st.info(
                "Каталог пуст. Добавьте товары на экране Каталог и загрузите цены."
            )
            return

        labels = {f"{p.sku} — {p.name}": p.id for p in products}
        label = st.selectbox("Товар", list(labels.keys()), key="history_product")
        product_id = labels[label]

        product = next(p for p in products if p.id == product_id)
        st.write(f"**Своя цена:** {product.own_price:,.2f} ₽".replace(",", " "))

        date_selection = st.date_input(
            "Период (необязательно)",
            value=[],
            key="history_period",
        )
        if len(date_selection) == 2:
            date_from = date_selection[0].isoformat()
            date_to = date_selection[1].isoformat()
        else:
            date_from = ""
            date_to = ""

        rows = history_service.product_prices(
            conn, product_id, date_from=date_from, date_to=date_to
        )

        if not rows:
            st.warning("По этому товару пока нет цен.")
            return

        df = series_frame(rows)
        fig = build_figure(df)
        st.plotly_chart(fig, use_container_width=True)

        st.subheader("Ряды цен")
        view = df.rename(
            columns={
                "price": "Цена, ₽",
                "price_date": "Дата цены",
                "source_name": "Источник",
            }
        )
        st.dataframe(
            view[["Цена, ₽", "Дата цены", "Источник"]],
            width="stretch",
            hide_index=True,
            column_config={
                "Цена, ₽": st.column_config.NumberColumn(format="%.2f ₽"),
                "Дата цены": st.column_config.DateColumn(format="DD.MM.YYYY"),
            },
        )

        st.download_button(
            "⬇️ Скачать CSV",
            data=history_csv(df).encode("utf-8-sig"),
            file_name="history.csv",
            mime="text/csv",
        )
    finally:
        conn.close()


if __name__ == "__main__":
    render()