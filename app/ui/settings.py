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

    tab_sources, tab_rules, tab_agent, tab_telegram = st.tabs(
        ["Источники", "Правила сопоставления", "ИИ-агент", "Telegram"]
    )

    with tab_sources:
        _sources_section()

    with tab_rules:
        _rules_section()

    with tab_agent:
        _agent_section()

    with tab_telegram:
        _telegram_section()


def _sources_section() -> None:
    st.subheader("Реестр источников")
    st.markdown(
        "Сбор возможен **только** из источников со статусом «Разрешён». "
        "Источник вводится с условиями использования и явным подтверждением."
    )

    with st.expander(
        "Добавить источник", expanded=False, icon=":material/add:"
    ):
        _form_add_source()

    _list_sources()

    with st.expander(
        "Изменить источник", expanded=False, icon=":material/edit:"
    ):
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
        submitted = st.form_submit_button("Добавить источник", icon=":material/add:")

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
            "Статус": STATUS_LABELS[s.status],
        }
        for s in sources
    ]
    df = pd.DataFrame(rows)

    # st.dataframe не понимает разметку `:red[...]` внутри ячеек — она
    # рендерится только в markdown (st.success/st.error). Цвет статуса
    # здесь красится через pandas Styler, который st.dataframe умеет.
    hex_by_color = {"green": "#1a7f37", "red": "#cf222e", "orange": "#9a6700"}
    row_colors = [hex_by_color.get(STATUS_COLORS.get(s.status, ""), "#57606a") for s in sources]

    styled = df.style.apply(
        lambda _col: [f"color:{c};font-weight:600" for c in row_colors],
        subset=["Статус"],
    )
    st.dataframe(styled, width="stretch", hide_index=True)


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
        save = col_a.form_submit_button(
            "Сохранить изменения", icon=":material/save:"
        )
        delete = col_b.form_submit_button(
            "Удалить источник", icon=":material/delete:"
        )

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


def agent_status(conn) -> tuple[bool, str]:
    """(работает ли реальный агент, текст статуса) — для Настроек и экранов."""
    from app.services.agent import load_agent

    adapter = load_agent(conn)
    if adapter.is_live:
        return True, f"Агент подключён: обоснования пишет модель «{adapter.model}»."
    gaps = ", ".join(adapter.config.missing())
    return False, (
        "Режим-заглушка: обращения к Timeweb не происходит, тексты собираются "
        f"локально по шаблону. Не заданы: {gaps}."
    )


def _agent_section() -> None:
    st.subheader("Параметры ИИ-агента (Timeweb)")

    conn = _conn()
    try:
        live, status_text = agent_status(conn)
        endpoint = settings_service.get_setting(conn, "agent_endpoint")
        api_key = settings_service.get_setting(conn, "agent_key")
        model = settings_service.get_setting(conn, "agent_model")
    finally:
        conn.close()

    if live:
        st.success(status_text)
    else:
        st.warning(status_text)

    st.markdown(
        "- Отбор позиций и все расчёты выполняются локально — модель пишет "
        "только текст обоснования.\n"
        "- Рекомендации агента — **не решения**: владелец применяет их вручную.\n"
        "- Каждый вызов логируется в `agent_log` с пометкой режима."
    )

    endpoint_new = st.text_input(
        "Эндпоинт Timeweb",
        value=endpoint,
        placeholder="https://agent.timeweb.cloud/api/v1/cloud-ai/agents/<id>/v1",
        key="agent_endpoint_input",
        icon=":material/link:",
    )
    api_key_new = st.text_input(
        "API-ключ",
        value=api_key,
        type="password",
        key="agent_key_input",
        icon=":material/key:",
        help="Хранится в локальной базе db/monitoring.db открытым текстом.",
    )
    model_new = st.text_input(
        "Модель",
        value=model,
        placeholder="имя модели из панели Timeweb — без него запрос не уйдёт",
        key="agent_model_input",
        icon=":material/auto_awesome:",
    )

    col_save, col_check = st.columns([1, 1])

    if col_save.button(
        "Сохранить параметры агента", key="agent_save", icon=":material/save:"
    ):
        conn = _conn()
        try:
            settings_service.set_setting(conn, "agent_endpoint", endpoint_new.strip())
            settings_service.set_setting(conn, "agent_key", api_key_new.strip())
            settings_service.set_setting(conn, "agent_model", model_new.strip())
            st.success("Параметры агента сохранены.")
            st.rerun()
        finally:
            conn.close()

    if col_check.button(
        "Проверить подключение", key="agent_check", icon=":material/power:"
    ):
        _check_agent_connection()


def _check_agent_connection() -> None:
    """Короткий проверочный запрос к модели сохранёнными параметрами."""
    from app.services.agent import AgentError, load_agent

    conn = _conn()
    try:
        adapter = load_agent(conn)
        with st.spinner("Отправляю проверочный запрос…"):
            try:
                answer = adapter.check_connection()
            except AgentError as exc:
                st.error(f"Связи нет. {exc}")
                st.caption(
                    "Проверьте эндпоинт, ключ и имя модели, затем нажмите "
                    "«Сохранить параметры агента» и повторите проверку."
                )
                return
        st.success(f"Связь есть. Модель ответила: «{answer[:200]}»")
    finally:
        conn.close()


def telegram_status(conn) -> tuple[bool, str]:
    """(настроен ли Telegram, текст статуса) — для Настроек."""
    from app.services import notifications

    cfg = notifications.config(conn)
    if cfg.configured:
        threshold = notifications.alert_threshold(conn)
        return True, (
            f"Уведомления включены: сообщение уйдёт, если конкурент дешевле "
            f"вас более чем на {threshold:.0f}%."
        )
    gaps = ", ".join(cfg.missing())
    return False, f"Уведомления выключены. Не заданы: {gaps}."


def _telegram_section() -> None:
    from app.core.report import clamp_threshold

    st.subheader("Уведомления в Telegram")

    # st.rerun() сразу после сохранения обрывает текущий прогон — сообщение,
    # выведенное перед ним, до экрана не доезжает. Поэтому текст кладётся в
    # session_state и показывается один раз уже на следующем прогоне.
    saved_message = st.session_state.pop("telegram_save_message", None)
    if saved_message:
        st.success(saved_message)

    conn = _conn()
    try:
        live, status_text = telegram_status(conn)
        bot_token = settings_service.get_setting(conn, "telegram_bot_token")
        chat_id = settings_service.get_setting(conn, "telegram_chat_id")
        threshold = float(
            settings_service.get_setting(conn, "telegram_alert_threshold_pct") or "15"
        )
    finally:
        conn.close()

    if live:
        st.success(status_text)
    else:
        st.warning(status_text)

    st.markdown(
        "- Проверяется только контур **«Конкуренты»** — сразу после каждого импорта.\n"
        "- Сообщение уходит один раз на загрузку: повторный импорт того же файла "
        "новых цен не создаёт и уведомление не дублирует.\n"
        "- Токен и chat_id хранятся в локальной базе `db/monitoring.db` "
        "открытым текстом."
    )

    with st.expander(
        "Как завести бота и узнать chat_id",
        key="telegram_help",
        icon=":material/help:",
    ):
        st.markdown(
            "1. В Telegram откройте **@BotFather**, отправьте `/newbot` "
            "и следуйте подсказкам — в конце придёт токен вида "
            "`123456789:AAExampleTokenTextHere`.\n"
            "2. Напишите вашему новому боту любое сообщение — иначе он не "
            "сможет писать вам первым.\n"
            "3. Откройте `https://api.telegram.org/bot<ТОКЕН>/getUpdates` "
            "в браузере (подставив свой токен) и найдите поле `\"chat\":{\"id\": ...}` "
            "— это и есть chat_id.\n"
            "4. Для группового чата добавьте бота в группу и отправьте туда "
            "сообщение — chat_id для групп отрицательный."
        )

    bot_token_new = st.text_input(
        "Токен бота",
        value=bot_token,
        type="password",
        key="telegram_token_input",
        placeholder="получен от @BotFather",
    )
    chat_id_new = st.text_input(
        "Chat ID",
        value=chat_id,
        key="telegram_chat_input",
        placeholder="ваш личный или групповой chat_id",
    )
    threshold_new = st.number_input(
        "Критический порог, %",
        min_value=0,
        max_value=100,
        value=int(clamp_threshold(threshold)),
        step=1,
        help="Уведомление уходит, если конкурент дешевле вашей цены более чем на этот процент.",
        key="telegram_threshold_input",
    )

    col_save, col_check = st.columns([1, 1])

    if col_save.button(
        "Сохранить параметры Telegram", key="telegram_save"
    ):
        conn = _conn()
        try:
            settings_service.set_setting(conn, "telegram_bot_token", bot_token_new.strip())
            settings_service.set_setting(conn, "telegram_chat_id", chat_id_new.strip())
            settings_service.set_setting(
                conn, "telegram_alert_threshold_pct", str(int(clamp_threshold(threshold_new)))
            )
            st.session_state["telegram_save_message"] = "Параметры Telegram сохранены."
            st.rerun()
        finally:
            conn.close()

    if col_check.button(
        "Отправить тестовое сообщение", key="telegram_check"
    ):
        _check_telegram_connection()


def _check_telegram_connection() -> None:
    from app.services import notifications, telegram

    conn = _conn()
    try:
        cfg = notifications.config(conn)
        with st.spinner("Отправляю тестовое сообщение…"):
            try:
                telegram.send_test(cfg)
            except telegram.TelegramError as exc:
                st.error(f"Не отправлено. {exc}")
                st.caption(
                    "Проверьте токен и chat_id, затем нажмите «Сохранить параметры "
                    "Telegram» и повторите проверку."
                )
                return
        st.success("Сообщение отправлено — проверьте чат в Telegram.")
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
        icon=":material/percent:",
    )
    manual_threshold = st.number_input(
        "Ручной порог (manual), %",
        min_value=0,
        max_value=100,
        value=int(float(default.get("manual_threshold", "70"))),
        step=1,
        key="settings_manual_threshold",
        icon=":material/percent:",
    )

    if manual_threshold >= auto_threshold:
        st.error("Ручной порог должен быть строго меньше автоматического.")
    elif st.button(
        "Сохранить пороги", key="thresholds_save", icon=":material/save:"
    ):
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