from __future__ import annotations

import sqlite3

import pandas as pd
import streamlit as st

from app.core.suppliers import diff_percent, pick_cheapest
from app.services import suppliers as suppliers_service
from app.storage.db import get_connection
from app.ui import agent_block


RAW_LABEL = "Разовый файл (поставщики)"

# Как на остальных экранах: на телефоне широкие таблицы «товар × поставщик»
# и сводки по товару превращаются в карточки, на десктопе остаются таблицами.
# Скрытие свойственно каждому блоку отдельно через ключевые контейнеры.
_SUPPLIERS_CSS = """
<style>
@media (max-width: 767px) {
  .st-key-suppliers_cards_matrix_phone,
  .st-key-suppliers_cards_stats_phone { display: block !important; }
  .st-key-suppliers_matrix_phone [data-testid="stDataFrame"] { display: none !important; }
  .st-key-suppliers_stats_phone [data-testid="stDataFrame"] { display: none !important; }
}
@media (min-width: 768px) {
  .st-key-suppliers_cards_matrix_phone,
  .st-key-suppliers_cards_stats_phone { display: none !important; }
}
</style>
"""


def _fmt_price(v: float) -> str:
    return f"{v:,.2f} ₽".replace(",", " ")


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
    with st.container(key="suppliers_matrix_phone"):
        st.dataframe(pivot, width="stretch", hide_index=True)
    with st.container(key="suppliers_cards_matrix_phone"):
        _matrix_cards(items)


def _matrix_cards(items: list[dict]) -> None:
    """Матрица «товар × поставщик» карточками — на телефоне читается целиком."""
    by_product: dict[int, list[dict]] = {}
    for it in items:
        by_product.setdefault(it["product_id"], []).append(it)
    for rows in by_product.values():
        first = rows[0]
        with st.container(border=True):
            st.markdown(f"**{first['product_name']}** — {first['sku']}")
            for r in rows:
                st.caption(
                    f"{r['source_name']}: {_fmt_price(r['price'])} "
                    f"(на {r['price_date']})"
                )


def _stats_cards(stats) -> None:
    """Сводка по поставщикам карточками — на телефоне."""
    for s in stats:
        with st.container(border=True):
            st.markdown(f"**{s.source_name}**")
            st.caption(f"Последняя цена: {_fmt_price(s.last_price)} · Дата: {s.last_date}")
            st.caption(
                f"Мин.: {_fmt_price(s.min_price)} · Макс.: {_fmt_price(s.max_price)} · "
                f"Средняя: {_fmt_price(s.avg_price)}"
            )
            st.caption(f"Период цен: {s.first_date} — {s.last_date}")


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
        has_raw = suppliers_service.has_raw_supplier_prices(conn)
        products = suppliers_service.list_products_with_supplier_prices(conn)

        with st.expander(
            "Выбор фильтров",
            expanded=True,
            key="suppliers_filters",
            icon=":material/filter_alt:",
        ):
            category_selected = st.selectbox(
                "Категория",
                ["(все категории)"] + categories,
                key="suppliers_category",
            )
            category = "" if category_selected == "(все категории)" else category_selected

            supplier_labels = ["(все поставщики)"]
            if has_raw:
                supplier_labels.append(RAW_LABEL)
            supplier_labels += [s["name"] for s in suppliers]
            supplier_name = st.selectbox(
                "Поставщик",
                supplier_labels,
                key="suppliers_source",
            )
            source_id = None
            if supplier_name == RAW_LABEL:
                source_id = suppliers_service.RAW_SOURCE_ID
            elif supplier_name != "(все поставщики)":
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

        st.markdown(_SUPPLIERS_CSS, unsafe_allow_html=True)
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
        with st.container(key="suppliers_stats_phone"):
            st.dataframe(stats_df, width="stretch", hide_index=True)
        with st.container(key="suppliers_cards_stats_phone"):
            _stats_cards(stats)

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
            "Скачать CSV (последние цены)",
            key="suppliers_download",
            icon=":material/download:",
            data=_matrix_csv(items).encode("utf-8-sig"),
            file_name="suppliers.csv",
            mime="text/csv",
        )

        agent_block.render(conn, "supplier", "source_switch")
    finally:
        conn.close()


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