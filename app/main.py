from __future__ import annotations

import streamlit as st

from app.storage.db import init_db
from app.ui import icons

APP_TITLE = "Ценовой мониторинг"

# Разделы меню. Порядок и группы — то, как владелец ведёт работу:
# сначала завести данные, потом смотреть выводы.
NAV = {
    "Разделы": [
        ("screens/catalog.py", "Каталог товаров", ":material/inventory_2:", "katalog"),
        ("screens/upload.py", "Загрузка цен", ":material/upload_file:", "zagruzka"),
        (
            "screens/matching.py",
            "Сопоставление товаров",
            ":material/compare_arrows:",
            "sopostavlenie",
        ),
    ],
    "Анализ": [
        (
            "screens/report.py",
            "Конкуренты: отклонения",
            ":material/trending_down:",
            "konkurenty",
        ),
        (
            "screens/suppliers.py",
            "Поставщики: закупка",
            ":material/local_shipping:",
            "postavshchiki",
        ),
        ("screens/history.py", "История цен", ":material/history:", "istoriya"),
    ],
    "Система": [
        ("screens/settings.py", "Настройки", ":material/settings:", "nastroyki"),
    ],
}


def _pages() -> dict[str, list[st.Page]]:
    pages: dict[str, list[st.Page]] = {}
    first = True
    for section, items in NAV.items():
        pages[section] = []
        for path, title, icon, url_path in items:
            pages[section].append(
                st.Page(path, title=title, icon=icon, url_path=url_path, default=first)
            )
            first = False
    return pages


def main() -> None:
    init_db()
    st.set_page_config(
        page_title=APP_TITLE,
        page_icon=":material/monitoring:",
        layout="wide",
    )

    page = st.navigation(_pages())

    # Подмена штатных иконок нашими SVG — один раз на отрисовку страницы,
    # до того как виджеты нарисуются.
    icons.inject()

    with st.sidebar:
        st.markdown(f"### {APP_TITLE}")
        st.caption("Конкуренты и поставщики")

    page.run()


if __name__ == "__main__":
    main()
