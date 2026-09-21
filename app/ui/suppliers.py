from __future__ import annotations

import sqlite3

import pandas as pd
import streamlit as st

from app.core.suppliers import diff_percent, pick_cheapest
from app.services import suppliers as suppliers_service
from app.storage.db import get_connection


def _conn() -> sqlite3.Connection:
    return get_connection()


def _render_matrix(items: list[dict]) -> None:
    if not items:
        return
    df = pd.DataFrame(items)
    pivot = df.pivot_table(
        index=["product_id", "sku", "product_name"],
        columns="source_name",
        values="price",
        aggfunc="first",
    ).reset_index()
    pivot = pivot.rename(
        columns={"product_id": "ID", "sku": "SKU", "product_name": "Товар"}
    )
    st.dataframe(pivot, width="stretch", hide_index=True)


def render() -> None:
    st.header("Поставщики: закупка")
    st.caption(
        "Сравнение закупочных цен: таблица «товар × поставщик», минимум/максимум/средняя "
        "по периоду и рекомендация по самому выгодному поставщику."
    )

    conn = _conn()
    try:
        categories = suppliers_service.list_supplier_categories(conn)
        suppliers = suppliers_service.list_supplier_sources(conn)
        products = suppliers_service.list_products_with_supplier_prices(conn)

        with st.expander("🔽 Выбор фильтров", expanded=True):
            category_selected = st.selectbox(
                "Категория",
                ["(все категории)"] + categories,
                key="suppliers_category",
            )
            category = "" if category_selected == "(все категории)" else category_selected

            supplier_labels = ["(все поставщики)"] + [s["name"] for s in suppliers]
            supplier_name = st.selectbox(
                "Поставщик",
                supplier_labels,
                key="suppliers_source",
            )
            source_id = None
            if supplier_name != "(все поставщики)":
                source_id = next(s["id"] for s in suppliers if s["name"] == supplier_name)

            date_selection = st.date_input(
                "Период цен (необязательно)",
                value=[],
                key="suppliers_period",
            )
            if len(date_selection) == 2:
                date_from = date_selection[0].isoformat()
                date_to = date_selection[1].isoformat()
            else:
                date_from = ""
                date_to = ""

        items = suppliers_service.latest_prices(
            conn,
            date_from=date_from,
            date_to=date_to,
            category=category,
            source_id=source_id,
        )

        if not products:
            st.info(
                "Нет сопоставленных цен поставщиков. Загрузите прайс "
                "поставщика на экране Загрузка и подтвердите сопоставления."
            )
            return

        st.subheader("Таблица «товар × поставщик» (последние цены)")
        _render_matrix(items)

        filtered_products = [
            p for p in products if (not category or p["category"] == category)
        ]
        if not filtered_products:
            st.info("Для выбранной категории нет товаров с ценами поставщиков.")
            return

        st.subheader("Сравнение по товару")
        product_labels = {
            f"{p['sku']} — {p['name']}": p["id"] for p in filtered_products
        }
        product_key = st.selectbox(
            "Товар",
            list(product_labels.keys()),
            key="suppliers_product",
        )
        product_id = product_labels[product_key]

        product = next(p for p in filtered_products if p["id"] == product_id)
        stats = suppliers_service.stats_for_product(
            conn, product_id, date_from=date_from, date_to=date_to
        )

        if not stats:
            st.warning("По этому товару нет цен в выбранном периоде.")
            return

        stats_df = pd.DataFrame(
            [
                {
                    "source_name": s.source_name,
                    "last_price": s.last_price,
                    "last_date": s.last_date,
                    "min_price": s.min_price,
                    "max_price": s.max_price,
                    "avg_price": s.avg_price,
                    "first_date": s.first_date,
                }
                for s in stats
            ]
        )
        stats_df = stats_df.rename(
            columns={
                "source_name": "Поставщик",
                "last_price": "Последняя цена, ₽",
                "last_date": "Дата последней",
                "min_price": "Мин., ₽",
                "max_price": "Макс., ₽",
                "avg_price": "Средняя, ₽",
                "first_date": "Дата первой",
            }
        )
        st.dataframe(stats_df, width="stretch", hide_index=True)

        cheapest = pick_cheapest(stats)
        if cheapest is not None:
            own = product["own_price"]
            pct = diff_percent(own, cheapest.last_price)
            if own > 0 and pct is not None:
                sign = "ниже" if pct <= 0 else "выше"
                msg = (
                    f"Самый выгодный поставщик — **{cheapest.source_name}** "
                    f"({cheapest.last_price:,.2f} ₽). Это на {abs(pct):.1f}% {sign} "
                    f"вашей цены {own:,.2f} ₽."
                )
            else:
                msg = (
                    f"Самый выгодный поставщик — **{cheapest.source_name}** "
                    f"({cheapest.last_price:,.2f} ₽)."
                )
            st.success(msg)

        st.download_button(
            "⬇️ Скачать CSV (последние цены)",
            data=_matrix_csv(items).encode("utf-8-sig"),
            file_name="suppliers.csv",
            mime="text/csv",
        )

        _agent_block(conn)
    finally:
        conn.close()


def _agent_block(conn: sqlite3.Connection) -> None:
    from app.services.agent import AgentError, load_agent

    if st.button("🤖 Запросить рекомендации агента", key="agents_suppliers"):
        with st.spinner("Агент анализирует прайсы поставщиков…"):
            try:
                recs = load_agent(conn).analyze_prices("supplier")
                st.success(
                    f"Агент подготовил {len(recs)} рекомендаций по закупке."
                )
            except AgentError as exc:
                st.error(str(exc))
            finally:
                st.rerun()


def _matrix_csv(items: list[dict]) -> str:
    import io

    buf = io.StringIO()
    if items:
        df = pd.DataFrame(items)
        visible = {
            "sku": "SKU",
            "product_name": "Товар",
            "category": "Категория",
            "source_name": "Поставщик",
            "price": "Последняя цена, ₽",
            "price_date": "Дата цены",
        }
        df = df[[c for c in visible if c in df.columns]].rename(columns=visible)
        df.to_csv(buf, index=False)
    return buf.getvalue()


if __name__ == "__main__":
    render()