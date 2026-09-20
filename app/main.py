from __future__ import annotations

import streamlit as st

from app.ui import catalog, history, matching, report, settings as settings_screen
from app.ui import suppliers, upload

PAGES = {
    "Каталог": catalog.render,
    "Загрузка": upload.render,
    "Сопоставление": matching.render,
    "Отчёт: конкуренты": report.render,
    "Прайсы: поставщики": suppliers.render,
    "История": history.render,
    "Настройки": settings_screen.render,
}


def main() -> None:
    st.set_page_config(page_title="Ценовой мониторинг", layout="wide")
    st.title("Ценовой мониторинг: конкуренты и поставщики")

    page = st.sidebar.radio("Раздел", list(PAGES.keys()), index=0)
    render = PAGES[page]
    render()


if __name__ == "__main__":
    main()