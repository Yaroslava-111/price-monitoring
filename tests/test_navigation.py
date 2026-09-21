"""Навигация: каждый пункт меню открывает свой экран и не падает."""

import pytest
from streamlit.testing.v1 import AppTest

from app.main import NAV
from app.storage import db as db_module

MAIN_SCRIPT = str(db_module.BASE_DIR / "app" / "main.py")

# ожидаемый заголовок экрана для каждой страницы меню
EXPECTED_HEADER = {
    "screens/catalog.py": "Каталог товаров",
    "screens/upload.py": "Загрузка цен",
    "screens/matching.py": "Сопоставление товаров",
    "screens/report.py": "Конкуренты: отклонения",
    "screens/suppliers.py": "Поставщики: закупка",
    "screens/history.py": "История цен",
    "screens/settings.py": "Настройки",
}


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    monkeypatch.setattr(db_module, "_db_path", lambda: tmp_path / "test.db")
    connection = db_module.init_db()
    yield connection
    connection.close()


def _nav_items():
    return [item for items in NAV.values() for item in items]


def test_menu_covers_every_screen():
    paths = [path for path, *_ in _nav_items()]
    assert sorted(paths) == sorted(EXPECTED_HEADER)


def test_menu_entries_are_distinct():
    items = _nav_items()
    titles = [title for _, title, *_ in items]
    urls = [url for *_, url in items]
    assert len(set(titles)) == len(titles)
    assert len(set(urls)) == len(urls)


def test_screen_files_exist():
    for path, *_ in _nav_items():
        assert (db_module.BASE_DIR / "app" / path).is_file(), path


@pytest.mark.parametrize("path", sorted(EXPECTED_HEADER))
def test_each_page_opens(conn, path):
    at = AppTest.from_file(MAIN_SCRIPT, default_timeout=30)
    at.run()
    assert not list(at.exception)

    at.switch_page(path).run()
    assert not list(at.exception), [e.message for e in at.exception]
    assert EXPECTED_HEADER[path] in [h.value for h in at.header]


def test_menu_title_matches_screen_header():
    """Название пункта меню и заголовок экрана не должны расходиться."""
    for path, title, _icon, _url in _nav_items():
        assert EXPECTED_HEADER[path] == title, path


def test_default_page_has_empty_url_path():
    """Streamlit игнорирует url_path у страницы по умолчанию и всегда отдаёт
    ей корневой адрес "/". Если явно указать что-то другое, Streamlit это
    молча проигнорирует, но прямой переход по «красивому» пути покажет
    тост «Page not found» (с откатом на верную страницу) — воспроизведено
    на живом приложении при подготовке скриншотов для README.
    """
    items = [item for group in NAV.values() for item in group]
    defaults = [item for item in items if item[0] == "screens/catalog.py"]
    assert len(defaults) == 1
    _path, _title, _icon, url_path = defaults[0]
    assert url_path == ""


def test_only_one_page_has_empty_url_path():
    """Ровно одна страница может быть «безадресной» — иначе неоднозначно,
    какая из них default."""
    items = [item for group in NAV.values() for item in group]
    empty = [item for item in items if item[3] == ""]
    assert len(empty) == 1
