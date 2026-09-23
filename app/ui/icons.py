"""Набор иконок приложения и подмена ими штатных иконок Streamlit.

Почему так, а не через параметр `icon=`
--------------------------------------
`st.button(icon=...)`, `st.text_input(icon=...)` и родственные принимают
только эмодзи или имя из Material Symbols — произвольный SVG туда не
передать. Поэтому виджету задаётся близкая иконка Material как запасной
вариант, а поверх накладывается CSS, подменяющий её нашим рисунком.

Подмена сделана через `mask-image`, а не `background-image`: маска
закрашивается `currentColor`, поэтому иконка сама принимает цвет текста —
корректно выглядит и в светлой теме, и в тёмной, и на красной кнопке.

Если Streamlit однажды поменяет внутренние атрибуты, селектор просто
перестанет совпадать и останется иконка Material. Пустого места не будет.

Единый стиль
------------
Сетка 20×20, обводка 1.5 px, скруглённые концы и стыки, без заливки,
цвет — `currentColor`.
"""

from __future__ import annotations

from urllib.parse import quote

import streamlit as st

VIEWBOX = "0 0 20 20"
STROKE_WIDTH = 1.5

# name -> внутренность <svg>. Все контуры нарисованы в одной сетке 20×20
# с отступом 3 px от края, чтобы иконки выглядели одного веса.
ICONS: dict[str, str] = {
    # --- действия ---
    "save": (
        '<path d="M4.75 3.5h7.09a1.5 1.5 0 0 1 1.06.44l2.66 2.66a1.5 1.5 0 0 1 .44 1.06'
        'v7.59a1.25 1.25 0 0 1-1.25 1.25H4.75A1.25 1.25 0 0 1 3.5 15.25V4.75A1.25 1.25 0 0 1 4.75 3.5Z"/>'
        '<path d="M6.75 16.5v-4.75h6.5v4.75"/>'
        '<path d="M6.75 3.5v3.25h4.5V3.5"/>'
    ),
    "plus": '<path d="M10 4.25v11.5"/><path d="M4.25 10h11.5"/>',
    "trash": (
        '<path d="M3.75 5.75h12.5"/>'
        '<path d="M8.25 5.75V4.6a1.1 1.1 0 0 1 1.1-1.1h1.3a1.1 1.1 0 0 1 1.1 1.1v1.15"/>'
        '<path d="M5.4 5.75l.68 9.5a1.25 1.25 0 0 0 1.25 1.16h5.34a1.25 1.25 0 0 0 1.25-1.16l.68-9.5"/>'
        '<path d="M8.5 8.75v4.5"/><path d="M11.5 8.75v4.5"/>'
    ),
    "pencil": (
        '<path d="M13.2 3.55a1.6 1.6 0 0 1 2.25 2.25L7.4 13.85l-3 .75.75-3Z"/>'
        '<path d="M11.9 4.85 14.15 7.1"/>'
    ),
    "download": (
        '<path d="M10 3.25v9.25"/>'
        '<path d="M6.25 8.75 10 12.5l3.75-3.75"/>'
        '<path d="M3.75 14v1.75a1.25 1.25 0 0 0 1.25 1.25h10a1.25 1.25 0 0 0 1.25-1.25V14"/>'
    ),
    "upload": (
        '<path d="M10 12.5V3.25"/>'
        '<path d="M6.25 7 10 3.25 13.75 7"/>'
        '<path d="M3.75 14v1.75a1.25 1.25 0 0 0 1.25 1.25h10a1.25 1.25 0 0 0 1.25-1.25V14"/>'
    ),
    "filter": '<path d="M3.25 4.5h13.5l-5.25 6.2v5.05l-3 1.75V10.7Z"/>',
    "search": '<circle cx="9" cy="9" r="4.75"/><path d="M12.6 12.6 16.5 16.5"/>',
    "check": '<path d="M4.5 10.4 8.2 14.1 15.5 6.4"/>',
    "cross": '<path d="M5.5 5.5l9 9"/><path d="M14.5 5.5l-9 9"/>',
    "chevron_down": '<path d="M5 7.75 10 12.75l5-5"/>',
    "chevron_up": '<path d="M5 12.25 10 7.25l5 5"/>',
    # --- ИИ и связь ---
    "spark": (
        '<path d="M8.5 3.5l1.45 3.55L13.5 8.5l-3.55 1.45L8.5 13.5l-1.45-3.55L3.5 8.5l3.55-1.45Z"/>'
        '<path d="M15 12.3l.75 1.95 1.95.75-1.95.75-.75 1.95-.75-1.95-1.95-.75 1.95-.75Z"/>'
    ),
    "plug": (
        '<path d="M7.5 2.75v3.5"/><path d="M12.5 2.75v3.5"/>'
        '<path d="M5.75 6.25h8.5v3a4.25 4.25 0 0 1-8.5 0Z"/>'
        '<path d="M10 13.5v3.75"/>'
    ),
    "link": (
        '<path d="M8.6 11.4a3 3 0 0 0 4.24 0l2.4-2.4a3 3 0 1 0-4.24-4.24l-1.2 1.2"/>'
        '<path d="M11.4 8.6a3 3 0 0 0-4.24 0l-2.4 2.4a3 3 0 1 0 4.24 4.24l1.2-1.2"/>'
    ),
    "key": (
        '<circle cx="6.5" cy="10" r="3.25"/>'
        '<path d="M9.75 10h6.5"/><path d="M13.5 10v2.5"/><path d="M15.75 10v1.75"/>'
    ),
    # --- данные ---
    "box": (
        '<path d="M10 3.25 16.5 6.6v6.8L10 16.75 3.5 13.4V6.6Z"/>'
        '<path d="M3.5 6.6 10 10l6.5-3.4"/><path d="M10 10v6.75"/>'
    ),
    "tag": (
        '<path d="M3.75 9.2V4.75a1 1 0 0 1 1-1H9.2a1 1 0 0 1 .7.3l6.05 6.05a1 1 0 0 1 0 1.4'
        'l-4.45 4.45a1 1 0 0 1-1.4 0L4.05 9.9a1 1 0 0 1-.3-.7Z"/>'
        '<circle cx="7.1" cy="7.1" r="1.1"/>'
    ),
    "ruble": (
        '<path d="M7.25 16.25V4.75h3.4a3.3 3.3 0 0 1 0 6.6H7.25"/>'
        '<path d="M5.5 11.35h6"/><path d="M5.5 13.85h6"/>'
    ),
    "percent": (
        '<path d="M15 5 5 15"/>'
        '<circle cx="6.9" cy="6.9" r="1.9"/><circle cx="13.1" cy="13.1" r="1.9"/>'
    ),
    "folder": (
        '<path d="M3.5 6.25a1.25 1.25 0 0 1 1.25-1.25h3.1a1 1 0 0 1 .8.4l.9 1.2h5.7'
        'a1.25 1.25 0 0 1 1.25 1.25v6.4a1.25 1.25 0 0 1-1.25 1.25H4.75'
        'A1.25 1.25 0 0 1 3.5 14.25Z"/>'
    ),
    "lines": '<path d="M4.5 5.75h11"/><path d="M4.5 10h11"/><path d="M4.5 14.25h7"/>',
    "calendar": (
        '<path d="M4.5 5.5h11a1.25 1.25 0 0 1 1.25 1.25v8.5A1.25 1.25 0 0 1 15.5 16.5h-11'
        'a1.25 1.25 0 0 1-1.25-1.25v-8.5A1.25 1.25 0 0 1 4.5 5.5Z"/>'
        '<path d="M3.25 9h13.5"/><path d="M7 3.5v3"/><path d="M13 3.5v3"/>'
    ),
    "trending_down": (
        '<path d="M3.5 6.5 8 11l3-3 5.5 5.5"/><path d="M12.5 13.5h4v-4"/>'
    ),
    "truck": (
        '<path d="M2.75 6.25a1 1 0 0 1 1-1h7.5a1 1 0 0 1 1 1v7.25h-9.5Z"/>'
        '<path d="M12.25 8.5h2.6a1 1 0 0 1 .83.45l1.4 2.1a1 1 0 0 1 .17.55v1.9h-5Z"/>'
        '<circle cx="6" cy="15.25" r="1.6"/><circle cx="14.25" cy="15.25" r="1.6"/>'
    ),
    "history": (
        '<path d="M3.75 10a6.25 6.25 0 1 0 1.9-4.5"/>'
        '<path d="M3.5 3.75v2.9h2.9"/>'
        '<path d="M10 6.5V10l2.5 1.6"/>'
    ),
    "settings": (
        '<circle cx="10" cy="10" r="2.4"/>'
        '<path d="M10 2.75l1.2 2.1 2.4-.35.55 2.36 2.1 1.2-1.3 2.04 1.3 2.04-2.1 1.2'
        '-.55 2.36-2.4-.35L10 17.25l-1.2-2.1-2.4.35-.55-2.36-2.1-1.2 1.3-2.04-1.3-2.04'
        ' 2.1-1.2.55-2.36 2.4.35Z"/>'
    ),
    "alert": (
        '<path d="M10 3.75 17 16.25H3Z"/>'
        '<path d="M10 8.5v3"/><path d="M10 13.9v.1"/>'
    ),
    "send": '<path d="M3.5 10 16.5 3.5 11.5 16.5 9 11 3.5 10Z"/><path d="M9 11 16.5 3.5"/>',
    "bell": (
        '<path d="M6 8.5a4 4 0 0 1 8 0v3.2l1.5 2.3h-11L6 11.7Z"/>'
        '<path d="M8.5 15.5a1.6 1.6 0 0 0 3 0"/>'
    ),
    "help": (
        '<circle cx="10" cy="10" r="6.75"/>'
        '<path d="M7.9 8a2.1 2.1 0 1 1 3.2 1.8c-.7.45-1.1.9-1.1 1.7v.3"/>'
        '<path d="M10 14.1v.1"/>'
    ),
}


def svg(name: str, size: int = 20, stroke: float = STROKE_WIDTH) -> str:
    """Инлайновый <svg> для вставки в markdown (unsafe_allow_html=True)."""
    body = ICONS[name]
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{VIEWBOX}" '
        f'width="{size}" height="{size}" fill="none" stroke="currentColor" '
        f'stroke-width="{stroke}" stroke-linecap="round" stroke-linejoin="round" '
        f'aria-hidden="true" focusable="false">{body}</svg>'
    )


def data_uri(name: str, stroke: float = STROKE_WIDTH) -> str:
    """SVG как data-URI для CSS-маски.

    Цвет намеренно не задаётся: маска красится `currentColor` уже в CSS.
    """
    markup = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{VIEWBOX}" '
        f'fill="none" stroke="#000" stroke-width="{stroke}" '
        f'stroke-linecap="round" stroke-linejoin="round">{ICONS[name]}</svg>'
    )
    return "data:image/svg+xml," + quote(markup, safe="")


# Какому виджету какая иконка. Ключ — это `key=` виджета; `*` внутри ключа
# заменяет переменную часть (у виджетов в цикле ключ зависит от id строки).
WIDGET_ICONS: dict[str, str] = {
    # Настройки → ИИ-агент
    "agent_endpoint_input": "link",
    "agent_key_input": "key",
    "agent_model_input": "spark",
    "agent_save": "save",
    "agent_check": "plug",
    # Настройки → правила сопоставления
    "settings_auto_threshold": "percent",
    "settings_manual_threshold": "percent",
    "thresholds_save": "save",
    # Настройки → Telegram
    "telegram_token_input": "key",
    "telegram_chat_input": "tag",
    "telegram_threshold_input": "percent",
    "telegram_save": "save",
    "telegram_check": "send",
    "telegram_help": "help",
    # Каталог
    "catalog_import_run": "upload",
    # Конкуренты
    "agent_run_*": "spark",
    "report_download": "download",
    # Поставщики
    "suppliers_filters": "filter",
    "suppliers_download": "download",
    # История
    "history_download": "download",
    # Загрузка — ключ зависит от контура
    "upload_run_*": "upload",
    "history_more": "chevron_down",
    "history_collapse": "chevron_up",
    # Сопоставление — ключи содержат id строки
    "mapping_*_confirm": "check",
    "mapping_*_reject": "cross",
    "mapping_*_agent": "spark",
}

# Кнопки внутри st.form не принимают key, поэтому ловим их по имени формы:
# Streamlit вешает на контейнер формы класс `st-key-<имя формы>`.
FORM_ICONS: dict[str, str] = {
    "add_source_form": "plus",
    "edit_source_form": "save",
    "add_product_form": "plus",
    "edit_product_form": "save",
}

# Боковое меню: у пунктов нет `key`, зато есть свой адрес — по нему и целимся,
# чтобы иконки навигации были из того же набора, что и в формах. Ключ "" —
# страница по умолчанию: Streamlit всегда отдаёт ей корневой адрес "/",
# другой url_path для неё не бывает (см. app/main.py).
NAV_ICONS: dict[str, str] = {
    "": "box",
    "zagruzka": "upload",
    "sopostavlenie": "link",
    "konkurenty": "trending_down",
    "postavshchiki": "truck",
    "istoriya": "history",
    "nastroyki": "settings",
}


def _selector(key: str) -> str:
    """CSS-селектор контейнера виджета по его `key`.

    Streamlit вешает на контейнер класс `st-key-<key>`. Для ключей с
    переменной частью собираем выборку по подстрокам класса.
    """
    if "*" not in key:
        return f".st-key-{key}"
    head, _, tail = key.partition("*")
    parts = [f'[class*="st-key-{head}"]']
    if tail:
        parts.append(f'[class*="{tail}"]')
    return "".join(parts)


def _rule(selector: str, name: str, size: str) -> str:
    uri = data_uri(name)
    return (
        f"{selector} {{"
        "font-size:0 !important;line-height:0 !important;"
        f"width:{size} !important;height:{size} !important;"
        "display:inline-block;flex:0 0 auto;"
        "background-color:currentColor;"
        f'-webkit-mask:url("{uri}") center/contain no-repeat;'
        f'mask:url("{uri}") center/contain no-repeat;'
        "}"
    )


ICON_SPAN = '[data-testid="stIconMaterial"]'


def css(
    widget_icons: dict[str, str] | None = None,
    size: str = "1.15rem",
    *,
    forms: dict[str, str] | None = None,
    nav: dict[str, str] | None = None,
) -> str:
    """<style> с подменой штатных иконок нашими.

    widget_icons: {ключ виджета (key=...): имя иконки из ICONS}
    forms:        {имя st.form: иконка для его кнопок}
    nav:          {url_path раздела: иконка пункта меню}
    """
    widget_icons = WIDGET_ICONS if widget_icons is None else widget_icons
    forms = FORM_ICONS if forms is None else forms
    nav = NAV_ICONS if nav is None else nav

    used = set(widget_icons.values()) | set(forms.values()) | set(nav.values())
    unknown = sorted(used - set(ICONS))
    if unknown:
        raise KeyError(f"Нет таких иконок: {', '.join(unknown)}")

    rules: list[str] = []
    for key, name in sorted(widget_icons.items()):
        rules.append(_rule(f"{_selector(key)} {ICON_SPAN}", name, size))
    for form, name in sorted(forms.items()):
        rules.append(_rule(f".st-key-{form} {ICON_SPAN}", name, size))
    for url_path, name in sorted(nav.items()):
        rules.append(_rule(f'a[href$="/{url_path}"] {ICON_SPAN}', name, size))
    return "<style>" + CAPTION_CSS + MOBILE_CSS + "".join(rules) + "</style>"


def inject(widget_icons: dict[str, str] | None = None, size: str = "1.15rem") -> None:
    """Подключает подмену иконок. Вызывается один раз за отрисовку страницы."""
    st.markdown(css(widget_icons, size), unsafe_allow_html=True)


def show(name: str, size: int = 20) -> None:
    """Иконка отдельным элементом — для заголовков и пустых состояний."""
    st.markdown(svg(name, size), unsafe_allow_html=True)


CAPTION_CSS = (
    ".ui-caption{display:flex;align-items:flex-start;gap:.45rem;"
    "font-size:.875rem;line-height:1.4;opacity:.7;margin:.25rem 0 .5rem;}"
    ".ui-caption svg{flex:0 0 auto;margin-top:.12rem;}"
)

# Мобильная вёрстка: на узких экранах у Streamlit нет горизонтального скролла.
# Колонки переносятся на новую строку, длинные слова и ссылки переносятся,
# виджеты и таблицы не раздвигают страницу дальше ширины экрана.
MOBILE_CSS = (
    "@media (max-width: 767px){"
    "html,body{overflow-x:hidden}"
    "[data-testid=\"stApp\"],[data-testid=\"stAppViewContainer\"]{overflow-x:hidden}"
    ".block-container{min-width:0;max-width:100%;padding-left:1rem;padding-right:1rem}"
    "/* колонки в ряд переносятся на новую строку, а не сжимаются до упора */"
    "[data-testid=\"stHorizontalBlock\"]{flex-wrap:wrap;row-gap:.5rem}"
    "[data-testid=\"stHorizontalBlock\"]>div{flex:0 0 100% !important;"
    "min-width:100% !important;max-width:100% !important}"
    "/* длинные слова, ключи и ссылки не растягивают страницу */"
    "p,li,td,th,[data-testid=\"stMarkdownContainer\"],"
    "[data-testid=\"stWidgetLabel\"]{overflow-wrap:anywhere;word-break:break-word}"
    "code,pre,textarea{white-space:pre-wrap;overflow-wrap:anywhere}"
    "/* виджеты ввода не раздуваются сверх колонки */"
    ".stTextInput,.stNumberInput,.stDateInput,.stTimeInput,.stSelectbox,"
    ".stMultiSelect,.stTextArea,.stCheckbox,.stRadio{min-width:0;max-width:100%}"
    "/* таблицы и графики скроллятся внутри себя, а не тянут страницу */"
    "[data-testid=\"stDataFrame\"],[data-testid=\"stTable\"],"
    "[data-testid=\"stPlotlyChart\"],.js-plotly-plot,.plotly-graph-div{max-width:100%}"
    "/* на телефоне скругления таблиц и карточек убираем */"
    "[data-testid=\"stDataFrame\"],[data-testid=\"stDataFrame\"] *,"
    "[data-testid=\"stTable\"],[data-testid=\"stTable\"] *{border-radius:0 !important}"
    "/* bordered-карточки в 1.64 сверстаны как stVerticalBlock со своим классом */"
    "[data-testid=\"stVerticalBlock\"]{border-radius:0 !important}"
    "/* вкладки переносятся на новую строку */"
    "[data-testid=\"stTabs\"] [role=\"tablist\"]{flex-wrap:wrap;row-gap:.25rem}"
    "}"
)


def caption(name: str, text: str, size: int = 16) -> None:
    """Подпись с нашей иконкой: у `st.caption` параметра icon нет."""
    st.markdown(
        f'<div class="ui-caption">{svg(name, size)}<span>{text}</span></div>',
        unsafe_allow_html=True,
    )
