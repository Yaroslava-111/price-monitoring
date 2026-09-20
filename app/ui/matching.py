from __future__ import annotations

import sqlite3

import pandas as pd
import streamlit as st

from app.services import catalog as catalog_service
from app.services import matching as matching_service
from app.services.agent import AgentAdapter, AgentError, latest_match_rationale
from app.storage.db import get_connection

PAGE_SIZE = 10


def _conn() -> sqlite3.Connection:
    return get_connection()


def _page_state() -> int:
    if "matching_page" not in st.session_state:
        st.session_state["matching_page"] = 0
    return int(st.session_state["matching_page"])


def render() -> None:
    st.header("Сопоставление товаров")
    st.caption(
        "Спорные пары (70–90%) требуют подтверждения. Отклонённые попадают в чёрный список."
    )

    conn = _conn()
    try:
        pending = matching_service.list_pending(conn)
        if not pending:
            st.success("Нет ожидающих сопоставлений. Все спорные пары обработаны.")
            return

        total = len(pending)
        page = _page_state()
        max_page = (total - 1) // PAGE_SIZE
        if page > max_page:
            page = max_page
            st.session_state["matching_page"] = page

        start = page * PAGE_SIZE
        page_rows = pending[start : start + PAGE_SIZE]

        st.markdown(f"Ожидают подтверждения: **{total}** пар (страница {page + 1}/{max_page + 1}).")

        products = catalog_service.list_products(conn, active_only=True)
        product_by_id = {p.id: p for p in products}
        label_by_id = {p.id: f"{p.sku} — {p.name}" for p in products}

        for row in page_rows:
            with st.container(border=True):
                mid = row
                cand = product_by_id.get(mid["product_id"])
                cand_label = label_by_id.get(
                    mid["product_id"], f"#{mid['product_id']} (не найден)"
                )

                st.markdown(f"**{mid['external_name']}**")
                if mid.get("sku"):
                    pass
                st.caption(
                    f"Внешний ключ: {mid['external_key'] or '—'} · "
                    f"сходство {mid['similarity']:.0f}% · метод {mid['method']}"
                )
                if mid.get("price") is not None:
                    st.caption(
                        f"Цена строки: {mid['price']:.2f} ₽ "
                        f"на {mid['price_date'] or '—'}"
                    )

                if mid.get("ai_recommended"):
                    st.info("🤖 Рекомендация агента: этот товар.")
                else:
                    st.caption("Предложено системой (фаззи-сопоставление).")

                rationale = latest_match_rationale(conn, mid["id"])
                if rationale:
                    st.caption(f"Обоснование агента: {rationale}")

                selected_label = st.selectbox(
                    "Товар в каталоге",
                    [cand_label] + [v for v in label_by_id.values() if v != cand_label],
                    key=f"mapping_{mid['id']}_product",
                )
                selected_id = next(
                    (pid for pid, lbl in label_by_id.items() if lbl == selected_label),
                    mid["product_id"],
                )

                col1, col2, col3 = st.columns([1, 1, 3])
                confirm = col1.button("Подтвердить", key=f"mapping_{mid['id']}_confirm")
                reject = col2.button("Отклонить", key=f"mapping_{mid['id']}_reject")
                ask_agent = col3.button("🤖 Спросить агента", key=f"mapping_{mid['id']}_agent")
                if ask_agent:
                    with st.spinner("Агент думает…"):
                        try:
                            AgentAdapter(conn).match_suggest(mid["id"])
                            st.success("Рекомендация агента готова.")
                        except AgentError as exc:
                            st.error(str(exc))
                        finally:
                            st.rerun()
                if confirm or reject:
                    with st.spinner("Сохранение…"):
                        try:
                            if confirm:
                                matching_service.confirm_mapping(
                                    conn, mid["id"], product_id=selected_id
                                )
                                st.success("Сопоставление подтверждено.")
                            else:
                                matching_service.mark_rejected(conn, mid["id"])
                                st.warning("Пара отклонена и добавлена в чёрный список.")
                        except matching_service.MatchingError as exc:
                            st.error(str(exc))
                        finally:
                            st.rerun()

        col1, col2, col3 = st.columns([1, 1, 3])
        prev = col1.button("← Предыдущая", key="matching_prev")
        nxt = col2.button("Следующая →", key="matching_next")
        if prev and page > 0:
            st.session_state["matching_page"] = page - 1
            st.rerun()
        if nxt and page < max_page:
            st.session_state["matching_page"] = page + 1
            st.rerun()
    finally:
        conn.close()


if __name__ == "__main__":
    render()