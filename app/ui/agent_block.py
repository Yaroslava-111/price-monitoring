"""Блок ИИ-агента: кнопка запроса и список полученных рекомендаций.

Один и тот же блок на экранах «Конкуренты» и «Поставщики» — раньше он был
продублирован, причём на поставщиках список рекомендаций не выводился вовсе.

Почему результат кладётся в session_state
-----------------------------------------
После запроса нужно перерисовать страницу, чтобы показать свежие
рекомендации. Но `st.rerun()` стирает всё, что было выведено до него, —
поэтому раньше сообщение об итоге пропадало мгновенно и выглядело так,
будто кнопка не сработала. Теперь итог сохраняется, переживает
перерисовку и показывается ровно один раз.
"""

from __future__ import annotations

import json
import sqlite3

import streamlit as st

from app.services.agent import AgentError, load_agent
from app.ui import icons

RESULT_KEY = "agent_last_result"

SPINNER = {
    "competitor": "Агент анализирует цены конкурентов…",
    "supplier": "Агент анализирует прайсы поставщиков…",
}

EMPTY_HINT = {
    "competitor": (
        "Агенту нечего предложить: нет позиций, где конкурент дешевле вашей цены. "
        "Загрузите свежие цены конкурентов или подтвердите сопоставления."
    ),
    "supplier": (
        "Агенту нечего предложить: нет сопоставленных цен поставщиков. "
        "Загрузите прайс поставщика и подтвердите сопоставления."
    ),
}


def render(conn: sqlite3.Connection, scope: str, rec_type: str) -> None:
    agent = load_agent(conn)

    if not agent.is_live:
        icons.caption(
            "alert",
            "Агент в режиме-заглушке: обоснования собираются локально "
            "по шаблону. Подключить модель — в Настройках.",
        )

    _show_last_result(scope)

    if st.button(
        "Запросить рекомендации агента",
        key=f"agent_run_{scope}",
        icon=":material/auto_awesome:",
    ):
        with st.spinner(SPINNER[scope]):
            _run(conn, agent, scope, rec_type)
        st.rerun()

    _render_list(conn, rec_type)


def _run(conn: sqlite3.Connection, agent, scope: str, rec_type: str) -> None:
    """Запрашивает рекомендации и запоминает итог до следующей отрисовки."""
    try:
        new_recs = agent.analyze_prices(scope)
    except AgentError as exc:
        st.session_state[RESULT_KEY] = {
            "scope": scope,
            "level": "error",
            "text": str(exc),
        }
        return

    total = _count_proposed(conn, rec_type)

    if new_recs:
        text = f"Готово: новых рекомендаций — {len(new_recs)}. Список ниже."
    elif total:
        text = (
            "Новых рекомендаций нет: по текущим данным агент уже всё предложил. "
            f"В списке ниже — {total}."
        )
    else:
        text = EMPTY_HINT[scope]

    # Если модель настроена, но не ответила, текст собран по шаблону —
    # об этом надо сказать, иначе подмена пройдёт незаметно.
    note = ""
    if agent.is_live and agent.last_llm_error:
        note = f" Модель не ответила ({agent.last_llm_error}), текст собран по шаблону."

    st.session_state[RESULT_KEY] = {
        "scope": scope,
        "level": "success" if (new_recs or total) else "info",
        "text": text + note,
    }


def _show_last_result(scope: str) -> None:
    """Показывает итог прошлого запроса один раз и забывает его."""
    result = st.session_state.get(RESULT_KEY)
    if not result or result.get("scope") != scope:
        return
    del st.session_state[RESULT_KEY]

    level = result.get("level", "info")
    text = result.get("text", "")
    if level == "error":
        st.error(text)
    elif level == "success":
        st.success(text)
    else:
        st.info(text)


def _count_proposed(conn: sqlite3.Connection, rec_type: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM recommendations "
        "WHERE rec_type = ? AND status = 'proposed'",
        (rec_type,),
    ).fetchone()
    return int(row["c"])


def _render_list(conn: sqlite3.Connection, rec_type: str) -> None:
    recommendations = _list_recommendations(conn, rec_type)
    if not recommendations:
        return

    st.subheader(f"Рекомендации агента ({len(recommendations)})")
    st.caption(
        "Это подсказки, а не изменения: цены в каталоге агент не трогает. "
        "Раскройте строку, чтобы прочитать обоснование."
    )
    for rec in recommendations:
        with st.expander(rec["title"]):
            st.write(rec["rationale"])
            if rec["facts"]:
                st.caption(rec["facts"])


def _list_recommendations(conn: sqlite3.Connection, rec_type: str) -> list[dict]:
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
        result.append(
            {
                "id": row["id"],
                "title": _title(row, payload),
                "rationale": payload.get("rationale", ""),
                "facts": _facts(payload),
            }
        )
    return result


def _money(value) -> str:
    """Цена с неразрывным разделителем тысяч.

    Форматируется только число: замена «,» на пробел во всей строке
    портила названия, в которых запятая стоит по делу.
    """
    return f"{float(value):,.2f}".replace(",", " ") + " ₽"


def _title(row, payload: dict) -> str:
    """В заголовке — главное число, чтобы список читался не раскрывая строк."""
    base = f"{row['sku']} — {row['product_name']}"
    target = payload.get("target_price")
    supplier_price = payload.get("supplier_price")
    if target is not None:
        return f"{base} · целевая цена {_money(target)}"
    if supplier_price is not None:
        supplier = payload.get("supplier", "поставщик")
        return f"{base} · {supplier}, {_money(supplier_price)}"
    return base


def _facts(payload: dict) -> str:
    parts = []
    pct = payload.get("deviation_pct")
    if pct is not None:
        parts.append(f"отклонение {float(pct):+.1f}%")
    if payload.get("competitor"):
        parts.append(f"источник: {payload['competitor']}")
    if payload.get("mode") == "mock":
        parts.append("текст по шаблону")
    elif payload.get("mode") == "live":
        parts.append("текст написан моделью")
    return " · ".join(parts)
