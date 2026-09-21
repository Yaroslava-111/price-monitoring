from __future__ import annotations

import io
import sqlite3

import pandas as pd
import streamlit as st

from app.core.report import clamp_threshold
from app.services import report as report_service
from app.services import settings as settings_service
from app.services.agent import AgentError, load_agent
from app.storage.db import get_connection

_SETTING_KEY = "deviation_threshold_pct"


def _conn() -> sqlite3.Connection:
    return get_connection()


def _apply_filters() -> tuple[float, str, int | None, str, str]:
    st.subheader("Фильтры")

    conn_filter = _conn()

    default_threshold = settings_service.get_setting(conn_filter, _SETTING_KEY)
    threshold = st.slider(
        "Порог N% (конкурент дешевле не меньше чем на N%)",
        min_value=0,
        max_value=100,
        value=int(clamp_threshold(float(default_threshold or "5"))),
        step=1,
        key="report_threshold",
    )

    categories = report_service.list_categories(conn_filter)
    category_options = ["(все категории)"] + categories
    category = st.selectbox(
        "Категория", category_options, key="report_category"
    )
    if category == "(все категории)":
        category = ""

    competitor_sources = report_service.list_competitor_sources(conn_filter)
    source_options = ["(все источники)"] + [s["name"] for s in competitor_sources]
    source_name = st.selectbox(
        "Источник", source_options, key="report_source"
    )
    source_id = None
    if source_name != "(все источники)":
        source_id = next(s["id"] for s in competitor_sources if s["name"] == source_name)

    date_selection = st.date_input(
        "Период цен (необязательно)",
        value=[],
        key="report_period",
    )
    if len(date_selection) == 2:
        date_from, date_to = date_selection
        date_from_s = date_from.isoformat() if date_from else ""
        date_to_s = date_to.isoformat() if date_to else ""
    else:
        date_from_s = ""
        date_to_s = ""
    conn_filter.close()
    return threshold, category, source_id, date_from_s, date_to_s


def _save_threshold(conn: sqlite3.Connection, threshold: float) -> None:
    settings_service.set_setting(
        conn, _SETTING_KEY, str(int(clamp_threshold(threshold)))
    )


def render() -> None:
    st.header("Конкуренты: отклонения")
    st.caption(
        "Только позиции, где цена конкурента ниже вашей более чем на порог N%. "
        "Источник и дата цены указаны для каждой строки."
    )

    threshold, category, source_id, date_from, date_to = _apply_filters()

    conn = _conn()
    try:
        _save_threshold(conn, threshold)

        rows = report_service.list_deviations(
            conn,
            threshold=threshold,
            category=category,
            source_id=source_id,
            date_from=date_from,
            date_to=date_to,
        )
        total = report_service.total_deviations(
            conn,
            category=category,
            source_id=source_id,
            date_from=date_from,
            date_to=date_to,
        )
    finally:
        conn.close()

    st.markdown(f"Показано **{len(rows)}** из **{total}** позиций конкурентов.")

    if not rows:
        st.info(
            "Нет позиций с отклонением ниже порога. Попробуйте снизить N% "
            "или загрузить новые цены конкурентов."
        )
        return

    df = pd.DataFrame(rows)
    visible = {
        "sku": "SKU",
        "product_name": "Товар",
        "category": "Категория",
        "own_price": "Ваша цена, ₽",
        "price": "Цена конкурента, ₽",
        "delta_rub": "Δ, ₽",
        "deviation_pct": "Δ, %",
        "source_name": "Источник",
        "price_date": "Дата цены",
    }
    df_display = df[list(visible)].rename(columns=visible)

    def _fmt_price(v: float) -> str:
        return f"{v:,.2f} ₽".replace(",", " ")

    st.dataframe(
        df_display,
        width="stretch",
        hide_index=True,
        column_config={
            "Ваша цена, ₽": st.column_config.NumberColumn(format="%.2f ₽"),
            "Цена конкурента, ₽": st.column_config.NumberColumn(format="%.2f ₽"),
            "Δ, ₽": st.column_config.NumberColumn(format="%.2f ₽"),
            "Δ, %": st.column_config.NumberColumn(format="%.1f %%"),
        },
    )

    st.caption("Самое сильное отклонение сверху (отрицательное Δ%).")

    _render_agent_block("competitor", "price_change")

    csv_buf = io.StringIO()
    df_display.to_csv(csv_buf, index=False)
    st.download_button(
        "⬇️ Скачать CSV",
        data=csv_buf.getvalue().encode("utf-8-sig"),
        file_name="report_competitors.csv",
        mime="text/csv",
    )


def _render_agent_block(scope: str, rec_type: str) -> None:
    conn = _conn()
    try:
        agent = load_agent(conn)
        if st.button("🤖 Запросить рекомендации агента", key=f"agent_{scope}"):
            with st.spinner("Агент анализирует цены…"):
                try:
                    recs = agent.analyze_prices(scope)
                    st.success(f"Агент подготовил {len(recs)} рекомендаций.")
                except AgentError as exc:
                    st.error(str(exc))
                finally:
                    st.rerun()

        recommendations = _list_recommendations(conn, rec_type)
        if recommendations:
            st.subheader("Рекомендации агента")
            for rec in recommendations:
                with st.expander(rec["title"]):
                    st.write(rec["rationale"])
    finally:
        conn.close()


def _list_recommendations(conn: sqlite3.Connection, rec_type: str) -> list[dict]:
    import json

    rows = conn.execute(
        """
        SELECT r.id, r.product_id, r.payload, p.sku, p.name AS product_name
        FROM recommendations r
        JOIN products p ON p.id = r.product_id
        WHERE r.rec_type = ? AND r.status = 'proposed'
        ORDER BY r.id DESC
        """,
        (rec_type,),
    ).fetchall()
    result: list[dict] = []
    for row in rows:
        try:
            payload = json.loads(row["payload"] or "{}")
        except ValueError:
            continue
        title = f"{row['sku']} — {row['product_name']}"
        result.append(
            {
                "id": row["id"],
                "title": title,
                "rationale": payload.get("rationale", ""),
            }
        )
    return result