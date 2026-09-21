from __future__ import annotations

import sqlite3

import pandas as pd
import streamlit as st

from app.services import loads as loads_service
from app.services import sources as sources_service
from app.storage.db import get_connection


# Куда попадут цены после импорта — показываем прямо под переключателем,
# иначе выбор контура никак не виден на экране.
TARGET_SCREEN = {
    "competitor": "Конкуренты: отклонения",
    "supplier": "Поставщики: закупка",
}

SCOPE_TITLE = {"competitor": "конкуренты", "supplier": "поставщики"}

# История загрузок: сколько строк показываем сразу и сколько добавляет
# каждое нажатие «Показать ещё».
HISTORY_FIRST = 20
HISTORY_STEP = 10
HISTORY_LIMIT_KEY = "upload_history_limit"


def _conn() -> sqlite3.Connection:
    return get_connection()


def render() -> None:
    st.header("Загрузка цен")
    st.caption("CSV/XLSX из разрешённого источника или разовый файл.")

    conn = _conn()
    try:
        scope_label = st.radio(
            "Контур данных",
            ["Конкуренты", "Поставщики"],
            horizontal=True,
            index=0,
        )
        scope = "competitor" if scope_label == "Конкуренты" else "supplier"
        st.caption(f"Цены из файла попадут в раздел «{TARGET_SCREEN[scope]}».")

        sources = sources_service.list_sources(conn, scope=scope)
        raw_label = (
            f"Разовый файл — {SCOPE_TITLE[scope]} (без регистрации источника)"
        )
        labels = [raw_label] + [
            f"{s.name} — {s.kind} ({s.status})" for s in sources
        ]
        selected = st.selectbox("Источник", labels, index=0)
        source_id = None
        source_status: str | None = None
        if selected != labels[0]:
            idx = labels.index(selected)
            source = sources[idx - 1]
            source_id = source.id
            source_status = source.status

        _import_block(conn, scope, source_id, source_status)
        _render_history(conn)
    finally:
        conn.close()


def _import_block(
    conn: sqlite3.Connection,
    scope: str,
    source_id: int | None,
    source_status: str | None,
) -> None:
    """Загрузка файла и импорт. Вынесено отдельно, чтобы ранние выходы
    не прятали историю загрузок под ними."""
    # Ключ привязан к контуру: без него Streamlit считает это одним и тем же
    # виджетом, и прикреплённый файл «переезжает» при переключении контура —
    # прайс поставщика можно было случайно импортировать как цены конкурентов.
    uploaded = st.file_uploader(
        "Файл с ценами (.csv, .xlsx)",
        type=["csv", "xlsx"],
        help="До 20 МБ, до 50 000 строк. UTF-8 или cp1251.",
        key=f"upload_file_{scope}",
    )

    data: bytes | None = None
    preview: pd.DataFrame | None = None
    headers: pd.DataFrame | None = None
    error_text: str | None = None

    if uploaded is None:
        st.info("Загрузите файл для предпросмотра и запуска импорта.")
        return

    data = uploaded.getvalue()
    try:
        df, _encoding = loads_service.read_upload(
            data,
            uploaded.name,
            int(loads_service._read_setting(conn, "max_upload_mb", "20")),
            int(loads_service._read_setting(conn, "max_upload_rows", "50000")),
        )
        preview = df.head(5)
        headers = loads_service.detect_headers(df)
        if "price" not in headers:
            error_text = "В файле не найдена колонка цены."
        elif "sku" not in headers and "name" not in headers:
            error_text = "В файле не найдена колонка SKU или названия товара."
    except loads_service.LoadError as exc:
        error_text = str(exc)

    st.subheader("Предпросмотр (первые 5 строк)")
    if preview is not None:
        st.dataframe(preview, width="stretch", hide_index=True)
    if error_text:
        st.error(error_text)
        return

    has_date_column = "date" in headers
    price_date = st.date_input(
        "Дата цен (снимок)",
        value=pd.Timestamp("today").date(),
        help="Если в файле нет колонки даты — используется эта дата.",
        key=f"upload_date_{scope}",
        disabled=has_date_column,
    )
    if has_date_column:
        st.caption(
            "В файле есть колонка даты — дата берётся из неё построчно, "
            "поле выше в этот раз не используется."
        )

    status_hint = ""
    can_run = source_id is not None and source_status == "approved"
    if source_id is not None and not can_run:
        status_hint = (
            "Источник должен быть разрешён (approved). Проверьте условия и чекбокс согласия в Настройках."
        )
    elif source_id is None:
        status_hint = (
            f"Разовый файл — цены войдут в раздел «{TARGET_SCREEN[scope]}» "
            "без привязки к реестровому источнику."
        )

    if status_hint:
        st.warning(status_hint)

    col1, col2, _ = st.columns([2, 1, 2])
    run = col1.button(
        f"Запустить импорт — {SCOPE_TITLE[scope]}",
        type="primary",
        key=f"upload_run_{scope}",
        icon=":material/upload:",
        disabled=not can_run and source_id is not None,
        help=(
            "Активна после валидации файла и при approved-источнике."
            if source_id is not None
            else "Разовый файл: импорт запускается сразу."
        ),
    )

    if run and data is not None:
        with st.spinner("Импортируем…"):
            try:
                result = loads_service.run_import(
                    conn,
                    data,
                    uploaded.name,
                    scope,
                    source_id=source_id,
                    run_by="manual",
                    price_date_override=None if has_date_column else price_date.isoformat(),
                )
            except loads_service.LoadError as exc:
                st.error(str(exc))
                return

        st.success(f"Импорт завершён (загрука #{result.load_id}).")
        m = result.as_dict()
        st.markdown(
            f"""
- **Всего строк:** {m['total_rows']}
- **Добавлено цен:** {m['ok_rows']}
- **На подтверждение (pending):** {m['pending_rows']}
- **Повторов (уже были):** {m['duplicate_rows']}
- **Ошибок:** {m['error_rows']}
"""
        )
        if result.pending_rows:
            st.info(
                "Часть строк требует подтверждения сопоставления — откройте раздел «Сопоставление товаров»."
            )
        if result.errors:
            with st.expander("Ошибки по строкам"):
                st.text("\n".join(result.errors))

        _notify_telegram(conn, result.load_id)


def _notify_telegram(conn: sqlite3.Connection, load_id: int) -> None:
    """Уведомляет о критических отклонениях, если Telegram настроен.

    Ошибка отправки не должна выглядеть как ошибка импорта — данные уже
    сохранены, поэтому здесь только предупреждение, а не st.error.
    """
    from app.services import notifications
    from app.services.telegram import TelegramError

    try:
        summary = notifications.notify_load(conn, load_id)
    except TelegramError as exc:
        st.warning(f"Импорт сохранён, но уведомление в Telegram не отправлено. {exc}")
        return
    if summary:
        st.info(summary)


def _render_history(conn: sqlite3.Connection) -> None:
    st.subheader("История загрузок")

    total = loads_service.count_loads(conn)
    if not total:
        st.info("Загрузок пока не было.")
        return

    # Сколько строк показываем сейчас. Счётчик живёт в session_state, чтобы
    # раскрытый список не схлопывался при каждой перерисовке страницы.
    limit = min(st.session_state.get(HISTORY_LIMIT_KEY, HISTORY_FIRST), total)
    rows = loads_service.list_loads(conn, limit=limit)

    df = pd.DataFrame(rows)
    df["scope"] = df["scope"].map(SCOPE_TITLE).fillna(df["scope"])
    df["source_name"] = df["source_name"].fillna("Разовый файл")
    df = df.rename(
        columns={
            "id": "№",
            "scope": "Контур",
            "source_name": "Источник",
            "status": "Статус",
            "file_name": "Файл",
            "total_rows": "Строк",
            "ok_rows": "Принято",
            "error_rows": "Ошибок",
            "started_at": "Начало",
            "finished_at": "Конец",
        }
    )
    st.dataframe(df, width="stretch", hide_index=True)

    shown = len(rows)
    st.caption(f"Показано {shown} из {total}.")

    if shown < total:
        remaining = total - shown
        step = min(HISTORY_STEP, remaining)
        if st.button(
            f"Показать ещё {step}",
            key="history_more",
            icon=":material/expand_more:",
        ):
            st.session_state[HISTORY_LIMIT_KEY] = shown + HISTORY_STEP
            st.rerun()
    elif total > HISTORY_FIRST:
        if st.button(
            "Свернуть",
            key="history_collapse",
            icon=":material/expand_less:",
        ):
            st.session_state[HISTORY_LIMIT_KEY] = HISTORY_FIRST
            st.rerun()


if __name__ == "__main__":
    render()
