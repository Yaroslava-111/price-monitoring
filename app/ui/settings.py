from __future__ import annotations

import sqlite3

import pandas as pd
import streamlit as st

from app.services import settings as settings_service
from app.services import sources as sources_service
from app.services.sources import SourceError
from app.storage.db import get_connection

SCOPE_LABELS = {"competitor": "Конкуренты", "supplier": "Поставщики"}
KIND_LABELS = {
    "csv_upload": "CSV-выгрузка",
    "api": "API",
    "price_feed": "Прайс-фид",
}
STATUS_LABELS = {"approved": "Разрешён", "blocked": "Заблокирован", "expired": "Срок истёк"}
STATUS_COLORS = {"approved": "green", "blocked": "red", "expired": "orange"}


def _conn() -> sqlite3.Connection:
    return get_connection()


def _status_badge(status: str) -> str:
    color = STATUS_COLORS.get(status, "grey")
    if color == "green":
        return f":green[{STATUS_LABELS[status]}]"
    if color == "red":
        return f":red[{STATUS_LABELS[status]}]"
    return f":orange[{STATUS_LABELS[status]}]"


def render() -> None:
    st.header("Настройки")
    st.caption("Реестр источников данных и параметры сопоставления.")

    tab_sources, tab_rules, tab_agent = st.tabs(
        ["Источники", "Правила сопоставления", "ИИ-агент"]
    )

    with tab_sources:
        _sources_section()

    with tab_rules:
        _rules_section()

    with tab_agent:
        _agent_section()


def _sources_section() -> None:
    st.subheader("Реестр источников")
    st.markdown(
        "Сбор возможен **только** из источников со статусом «Разрешён». "
        "Источник вводится с условиями использования и явным подтверждением."
    )

    with st.expander("➕ Добавить источник", expanded=False):
        _form_add_source()

    _list_sources()

    with st.expander("✏️ Изменить источник", expanded=False):
        _form_edit_source()


def _form_add_source() -> None:
    with st.form("add_source_form"):
        name = st.text_input("Название источника", help="До 100 символов")
        col1, col2 = st.columns(2)
        scope = col1.selectbox(
            "Контур данных", list(SCOPE_LABELS.keys()), format_func=SCOPE_LABELS.get
        )
        kind = col2.selectbox(
            "Тип источника", list(KIND_LABELS.keys()), format_func=KIND_LABELS.get
        )
        origin_url = st.text_input(
            "Официальный адрес (URL)",
            help="Для API и прайс-фида обязателен",
        )
        terms_hash = st.text_input(
            "Ссылка на условия использования",
            placeholder="https://… / путь к файлу",
        )
        col3, col4 = st.columns(2)
        min_refresh = col3.number_input(
            "Мин. интервал между сборами, мин.",
            min_value=0,
            value=0,
            step=15,
        )
        review_days = col4.number_input(
            "Период проверки, дней",
            min_value=1,
            value=365,
            step=30,
        )
        allowed_fields = st.text_input(
            "Разрешённые поля (через запятую)",
            placeholder="sku, name, price, date",
        )
        usage_limits = st.text_input(
            "Ограничения использования (частота, лимиты, только для чтения и т.п.)"
        )
        col5, col6 = st.columns(2)
        attribution = col5.checkbox("Обязательно показывать источник", value=True)
        terms_agreed = col6.checkbox(
            "Условия использования изучены, использование разрешено",
            help="Без подтверждения источник получит статус «Заблокирован»",
        )
        submitted = st.form_submit_button("Добавить источник")

    if submitted:
        conn = _conn()
        try:
            source = sources_service.create_source(
                conn,
                name=name,
                scope=scope,
                kind=kind,
                origin_url=origin_url,
                terms_hash=terms_hash,
                terms_agreed=terms_agreed,
                allowed_fields=allowed_fields,
                usage_limits=usage_limits,
                min_refresh_minutes=min_refresh,
                requires_attribution=attribution,
                review_period_days=review_days,
            )
            st.success(f"Источник «{source.name}» — {_status_badge(source.status)}")
            st.rerun()
        except SourceError as exc:
            st.error(str(exc))
        finally:
            conn.close()


def _list_sources() -> None:
    conn = _conn()
    try:
        sources = sources_service.list_sources(conn)
    finally:
        conn.close()

    if not sources:
        st.info("Реестр источников пуст. Добавьте первый источник.")
        return

    rows = [
        {
            "Название": s.name,
            "Контур": SCOPE_LABELS[s.scope],
            "Тип": KIND_LABELS[s.kind],
            "URL": s.origin_url or "—",
            "Атрибуция": "да" if s.requires_attribution else "нет",
            "Интервал, мин": s.min_refresh_minutes,
            "Статус": _status_badge(s.status),
        }
        for s in sources
    ]
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)


def _form_edit_source() -> None:
    conn = _conn()
    try:
        sources = sources_service.list_sources(conn)
    finally:
        conn.close()

    if not sources:
        st.info("Реестр пуст — изменять пока нечего.")
        return

    by_label = {s.name: s for s in sources}
    label = st.selectbox("Выберите источник", list(by_label.keys()))
    source = by_label[label]

    with st.form("edit_source_form"):
        name = st.text_input("Название источника", value=source.name)
        col1, col2 = st.columns(2)
        scope = col1.selectbox(
            "Контур данных",
            list(SCOPE_LABELS.keys()),
            index=list(SCOPE_LABELS.keys()).index(source.scope),
            format_func=SCOPE_LABELS.get,
        )
        kind = col2.selectbox(
            "Тип источника",
            list(KIND_LABELS.keys()),
            index=list(KIND_LABELS.keys()).index(source.kind),
            format_func=KIND_LABELS.get,
        )
        origin_url = st.text_input("Официальный адрес (URL)", value=source.origin_url)
        terms_hash = st.text_input(
            "Ссылка на условия использования", value=source.terms_hash
        )
        col3, col4 = st.columns(2)
        min_refresh = col3.number_input(
            "Мин. интервал между сборами, мин.",
            min_value=0,
            value=int(source.min_refresh_minutes),
            step=15,
        )
        review_days = col4.number_input(
            "Период проверки, дней",
            min_value=1,
            value=int(source.review_period_days),
            step=30,
        )
        allowed_fields = st.text_input(
            "Разрешённые поля (через запятую)", value=source.allowed_fields
        )
        usage_limits = st.text_input(
            "Ограничения использования", value=source.usage_limits
        )
        col5, col6 = st.columns(2)
        attribution = col5.checkbox(
            "Обязательно показывать источник", value=source.requires_attribution
        )
        terms_agreed = col6.checkbox(
            "Условия использования изучены, использование разрешено",
            value=source.terms_agreed,
        )
        col_a, col_b = st.columns(2)
        save = col_a.form_submit_button("Сохранить изменения")
        delete = col_b.form_submit_button("Удалить источник")

    if save:
        conn = _conn()
        try:
            updated = sources_service.update_source(
                conn,
                source.id,
                name=name,
                scope=scope,
                kind=kind,
                origin_url=origin_url,
                terms_hash=terms_hash,
                terms_agreed=terms_agreed,
                allowed_fields=allowed_fields,
                usage_limits=usage_limits,
                min_refresh_minutes=min_refresh,
                requires_attribution=attribution,
                review_period_days=review_days,
            )
            st.success(f"Сохранено: {updated.name} — {_status_badge(updated.status)}")
            st.rerun()
        except SourceError as exc:
            st.error(str(exc))
        finally:
            conn.close()

    if delete:
        conn = _conn()
        try:
            sources_service.delete_source(conn, source.id)
            st.success("Источник удалён.")
            st.rerun()
        finally:
            conn.close()


def _agent_section() -> None:
    st.subheader("Параметры ИИ-агента (Timeweb)")
    st.markdown(
        "- Все вызовы агента проходят через `AgentAdapter` и логируются в `agent_log`.\n"
        "- Рекомендации агента — **не решения**: владелец применяет их вручную.\n"
        "- Мок-режим: пока endpoint пустой — ответы генерируются детерминированно локально."
    )

    conn = _conn()
    try:
        endpoint = settings_service.get_setting(conn, "agent_endpoint")
        api_key = settings_service.get_setting(conn, "agent_key")
        model = settings_service.get_setting(conn, "agent_model")
    finally:
        conn.close()

    endpoint_new = st.text_input(
        "Эндпоинт Timeweb",
        value=endpoint,
        placeholder="https://…/api (пусто = мок-режим)",
        key="agent_endpoint_input",
    )
    api_key_new = st.text_input(
        "API-ключ",
        value=api_key,
        type="password",
        key="agent_key_input",
    )
    model_new = st.text_input(
        "Модель",
        value=model,
        placeholder="например: timeweb-ai/gpt-…",
        key="agent_model_input",
    )

    if st.button("💾 Сохранить параметры агента", key="agent_save"):
        conn = _conn()
        try:
            settings_service.set_setting(conn, "agent_endpoint", endpoint_new.strip())
            settings_service.set_setting(conn, "agent_key", api_key_new.strip())
            settings_service.set_setting(conn, "agent_model", model_new.strip())
            st.success("Параметры агента сохранены.")
            st.rerun()
        finally:
            conn.close()


def _rules_section() -> None:
    st.subheader("Правила сопоставления")
    st.markdown(
        "Пороги в процентах (0–100). Требуется `manual < auto`: "
        "ниже ручного порога — «не сопоставлено», выше автоматического — "
        "автоподтверждение при единственном кандидате, между ними — спорная зона."
    )

    conn = _conn()
    try:
        default = settings_service.get_all_settings(conn)
    finally:
        conn.close()

    auto_threshold = st.number_input(
        "Автоматический порог (auto), %",
        min_value=0,
        max_value=100,
        value=int(float(default.get("auto_threshold", "90"))),
        step=1,
        key="settings_auto_threshold",
    )
    manual_threshold = st.number_input(
        "Ручной порог (manual), %",
        min_value=0,
        max_value=100,
        value=int(float(default.get("manual_threshold", "70"))),
        step=1,
        key="settings_manual_threshold",
    )

    if manual_threshold >= auto_threshold:
        st.error("Ручной порог должен быть строго меньше автоматического.")
    elif st.button("💾 Сохранить пороги", key="thresholds_save"):
        conn = _conn()
        try:
            settings_service.set_setting(conn, "auto_threshold", str(int(auto_threshold)))
            settings_service.set_setting(conn, "manual_threshold", str(int(manual_threshold)))
            st.success("Пороги сопоставления сохранены.")
            st.rerun()
        finally:
            conn.close()

    st.markdown(
        f"- **Точный SKU** — автоподтверждение (правило 1).\n"
        f"- **Нечёткое название ≥ {auto_threshold:.0f}%** (один кандидат) — автоподтверждение (правило 2).\n"
        f"- **{manual_threshold:.0f}–{auto_threshold:.0f}%** — спорная зона, требует подтверждения владельца.\n"
        f"- **< {manual_threshold:.0f}%** — «не сопоставлено»."
    )


if __name__ == "__main__":
    render()