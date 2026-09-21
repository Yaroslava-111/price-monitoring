from __future__ import annotations

import sqlite3

import pandas as pd
import streamlit as st

from app.services import loads as loads_service
from app.services import sources as sources_service
from app.storage.db import get_connection


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

        sources = sources_service.list_sources(conn, scope=scope)
        labels = ["Разовый файл (без регистрации источника)"] + [
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

        uploaded = st.file_uploader(
            "Файл с ценами (.csv, .xlsx)",
            type=["csv", "xlsx"],
            help="До 20 МБ, до 50 000 строк. UTF-8 или cp1251.",
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

        price_date = st.date_input(
            "Дата цен (снимок)",
            value=pd.Timestamp("today").date(),
            help="Если в файле нет колонки даты — используется эта дата.",
        )

        status_hint = ""
        can_run = source_id is not None and source_status == "approved"
        if source_id is not None and not can_run:
            status_hint = (
                "Источник должен быть разрешён (approved). Проверьте условия и чекбокс согласия в Настройках."
            )
        elif source_id is None:
            status_hint = "Разовый файл — цены войдут в историю без привязки к реестровому источнику."

        if status_hint:
            st.warning(status_hint)

        col1, col2, _ = st.columns([2, 1, 2])
        run = col1.button(
            "Запустить импорт",
            type="primary",
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
                        price_date_override=price_date.isoformat(),
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
                    "Часть строк требует подтверждения сопоставления — откройте экран «Сопоставление»."
                )
            if result.errors:
                with st.expander("Ошибки по строкам"):
                    st.text("\n".join(result.errors))

        st.subheader("История загрузок (последние 15)")
        st.dataframe(
            pd.DataFrame(loads_service.list_loads(conn, limit=15)),
            width="stretch",
            hide_index=True,
        )
    finally:
        conn.close()


if __name__ == "__main__":
    render()