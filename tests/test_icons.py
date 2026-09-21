"""Набор иконок: целостность рисунков и связность с интерфейсом."""

import re
import xml.etree.ElementTree as ET
from urllib.parse import unquote

import pytest

from app.main import NAV
from app.storage import db as db_module
from app.ui import icons

UI_DIR = db_module.BASE_DIR / "app" / "ui"


def _ui_source() -> str:
    return "\n".join(
        p.read_text(encoding="utf-8")
        for p in sorted(UI_DIR.glob("*.py"))
        if p.name != "icons.py"
    )


# ---- сами рисунки ----


@pytest.mark.parametrize("name", sorted(icons.ICONS))
def test_icon_is_valid_svg(name):
    """Каждая иконка должна разбираться как корректный SVG."""
    root = ET.fromstring(icons.svg(name))
    assert root.tag.endswith("svg")
    assert root.get("viewBox") == icons.VIEWBOX
    assert len(list(root)) >= 1, "иконка без контуров"


@pytest.mark.parametrize("name", sorted(icons.ICONS))
def test_icon_uses_current_color(name):
    """Цвет не зашит: иконка обязана подхватывать цвет текста."""
    markup = icons.svg(name)
    assert 'stroke="currentColor"' in markup
    assert 'fill="none"' in markup
    assert not re.search(r'(stroke|fill)="#[0-9a-fA-F]{3,6}"', markup)


@pytest.mark.parametrize("name", sorted(icons.ICONS))
def test_path_data_is_well_formed(name):
    """В контурах только допустимые команды SVG — ловит опечатку в букве.

    Размер и положение так не проверить: в path-данных перемешаны
    абсолютные координаты и относительные смещения, а запись вида «.83.45»
    означает два числа. Разбирать это без парсера бессмысленно, поэтому
    проверяем грамматику команд, а сам рисунок смотрим глазами.
    """
    root = ET.fromstring(icons.svg(name))
    paths = [el.get("d") for el in root if el.tag.endswith("path")]
    circles = [el for el in root if el.tag.endswith("circle")]
    assert paths or circles, f"{name}: нет ни контуров, ни окружностей"

    for d in paths:
        assert d, f"{name}: пустой атрибут d"
        letters = set(re.findall(r"[A-Za-z]", d))
        unknown = letters - set("MmLlHhVvCcSsQqTtAaZz")
        assert not unknown, f"{name}: недопустимые команды {unknown}"
        assert d.lstrip()[0] in "Mm", f"{name}: контур должен начинаться с M"

    for circle in circles:
        for attr in ("cx", "cy", "r"):
            assert circle.get(attr), f"{name}: у окружности нет {attr}"
        assert float(circle.get("r")) > 0


@pytest.mark.parametrize("name", sorted(icons.ICONS))
def test_data_uri_round_trip(name):
    uri = icons.data_uri(name)
    assert uri.startswith("data:image/svg+xml,")
    decoded = unquote(uri.split(",", 1)[1])
    ET.fromstring(decoded)
    # в кавычки CSS-правила data-URI попадает целиком — двойных кавычек быть не должно
    assert '"' not in uri


def test_icon_names_are_snake_case():
    for name in icons.ICONS:
        assert re.fullmatch(r"[a-z][a-z_]*", name), name


# ---- связность с интерфейсом ----


def _all_mappings():
    return {**icons.WIDGET_ICONS, **icons.FORM_ICONS, **icons.NAV_ICONS}


def test_every_mapping_points_to_existing_icon():
    unknown = sorted(set(_all_mappings().values()) - set(icons.ICONS))
    assert unknown == []


def test_nav_icons_match_menu():
    """Иконки меню заведены ровно на те разделы, что есть в навигации."""
    url_paths = {url for items in NAV.values() for *_, url in items}
    assert set(icons.NAV_ICONS) == url_paths


def test_widget_keys_exist_in_ui():
    """Мапинг не должен ссылаться на виджеты, которых уже нет в коде."""
    source = _ui_source()
    missing = []
    for key in icons.WIDGET_ICONS:
        needle = key.split("*")[0]
        if needle not in source:
            missing.append(key)
    assert missing == [], f"нет таких виджетов: {missing}"


def test_form_names_exist_in_ui():
    source = _ui_source()
    missing = [form for form in icons.FORM_ICONS if f'st.form("{form}"' not in source]
    assert missing == []


def test_no_emoji_left_in_widget_labels():
    """Эмодзи в подписях заменены иконками — проверяем, что не вернулись."""
    leftovers = re.findall(r"[\U0001F300-\U0001FAFF]", _ui_source())
    assert leftovers == [], f"остались эмодзи: {set(leftovers)}"


# ---- генерация CSS ----


def test_css_covers_all_targets():
    css = icons.css()
    for key in icons.WIDGET_ICONS:
        assert icons._selector(key) in css
    for form in icons.FORM_ICONS:
        assert f".st-key-{form}" in css
    for url_path in icons.NAV_ICONS:
        assert f'a[href$="/{url_path}"]' in css


def test_css_uses_mask_so_icons_follow_text_color():
    css = icons.css()
    assert "background-color:currentColor" in css
    assert "-webkit-mask:url(" in css
    assert "background-image" not in css


def test_css_rejects_unknown_icon():
    with pytest.raises(KeyError):
        icons.css({"some_key": "нет-такой-иконки"})


@pytest.mark.parametrize(
    "key,expected",
    [
        ("agent_save", ".st-key-agent_save"),
        ("mapping_*_agent", '[class*="st-key-mapping_"][class*="_agent"]'),
        ("upload_run_*", '[class*="st-key-upload_run_"]'),
    ],
)
def test_selector_shapes(key, expected):
    assert icons._selector(key) == expected


def test_css_is_a_single_style_block():
    css = icons.css()
    assert css.startswith("<style>")
    assert css.endswith("</style>")
    assert css.count("<style>") == 1
