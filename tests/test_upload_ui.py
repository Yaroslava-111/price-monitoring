import pytest
from streamlit.testing.v1 import AppTest

from app.storage import db as db_module

MAIN_SCRIPT = str(db_module.BASE_DIR / "app" / "main.py")


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    monkeypatch.setattr(db_module, "_db_path", lambda: tmp_path / "test.db")
    connection = db_module.init_db()
    yield connection
    connection.close()


def test_upload_screen_renders(conn):
    at = AppTest.from_file(MAIN_SCRIPT, default_timeout=30)
    at.run()
    assert not list(at.exception)

    at.switch_page("screens/upload.py")
    at.run()
    assert not list(at.exception)
    assert [h.value for h in at.header] == ["Загрузка цен"]
    # переключатель контура на месте
    assert any(r.label == "Контур данных" for r in at.radio)


def test_upload_import_full_flow(conn):
    from app.services import catalog as catalog_service
    from app.services import sources as sources_service

    catalog_service.create_product(conn, "COF-1", "Кофе Арабика 250г", 300.0)
    sources_service.create_source(
        conn,
        name="К1",
        scope="competitor",
        kind="csv_upload",
        origin_url="https://k1.example",
        terms_agreed=True,
    )
    conn.close()

    at = AppTest.from_file(MAIN_SCRIPT, default_timeout=30)
    at.run()
    at.switch_page("screens/upload.py")
    at.run()
    assert len(at.file_uploader) == 1

    at.file_uploader[0].set_value(
        (
            "k1.csv",
            "sku,цена,дата\nCOF-1,299.90,2026-09-01\n".encode("utf-8"),
            "text/csv",
        )
    )
    at.run()
    assert not list(at.exception)

    import_button = next(
        b for b in at.button if b.label.startswith("Запустить импорт")
    )
    import_button.click()
    at.run()
    assert not list(at.exception)

    success_msgs = [m.value for m in at.get("success")]
    assert any("Импорт завершён" in s for s in success_msgs)

    conn2 = db_module.init_db()
    try:
        loads = conn2.execute(
            "SELECT status, ok_rows, error_rows FROM loads ORDER BY id DESC"
        ).fetchone()
        assert loads["status"] == "success"
        assert loads["ok_rows"] == 1
        assert conn2.execute("SELECT COUNT(*) n FROM prices").fetchone()["n"] == 1
    finally:
        conn2.close()


def test_upload_rejects_duplicate_source_upload(conn):
    from app.services import catalog as catalog_service
    from app.services import loads as loads_service
    from app.services import sources as sources_service

    catalog_service.create_product(conn, "COF-1", "Кофе Арабика 250г", 300.0)
    src = sources_service.create_source(
        conn,
        name="К1",
        scope="competitor",
        kind="csv_upload",
        origin_url="https://k1.example",
        terms_agreed=True,
    )
    data = "sku,цена\nCOF-1,10\n".encode("utf-8")
    first = loads_service.run_import(conn, data, "k1.csv", "competitor", src.id)
    second = loads_service.run_import(conn, data, "k1.csv", "competitor", src.id)
    assert first.ok_rows == 1
    assert second.ok_rows == 0
    assert second.duplicate_rows == 1
    conn.close()


def test_uploaded_file_does_not_leak_between_scopes(conn):
    """Файл, прикреплённый в одном контуре, не должен «переезжать» в другой.

    До появления ключа `upload_file_<scope>` у st.file_uploader Streamlit считал
    это одним виджетом: прайс поставщика оставался прикреплённым после
    переключения на конкурентов и его можно было импортировать не в тот контур.
    """
    from app.services import catalog as catalog_service

    catalog_service.create_product(conn, "COF-1", "Кофе Арабика 250г", 300.0)
    conn.close()

    at = AppTest.from_file(MAIN_SCRIPT, default_timeout=30)
    at.run()
    at.switch_page("screens/upload.py")
    at.run()

    scope_radio = next(r for r in at.radio if r.label == "Контур данных")
    scope_radio.set_value("Поставщики")
    at.run()

    at.file_uploader[0].set_value(
        (
            "supplier.csv",
            "sku,закупка\nCOF-1,210.00\n".encode("utf-8"),
            "text/csv",
        )
    )
    at.run()
    assert at.file_uploader[0].value is not None
    assert any("Запустить импорт" in b.label for b in at.button)

    scope_radio = next(r for r in at.radio if r.label == "Контур данных")
    scope_radio.set_value("Конкуренты")
    at.run()

    assert not list(at.exception)
    assert at.file_uploader[0].value is None
    # без файла импорт запустить нельзя — кнопки просто нет
    assert not any("Запустить импорт" in b.label for b in at.button)


def _history_rows(at):
    """Строки таблицы истории загрузок (последний dataframe на экране)."""
    return at.dataframe[-1].value


def _seed_loads(conn, count):
    for i in range(count):
        conn.execute(
            "INSERT INTO loads (source_id, scope, kind, status, file_name, total_rows) "
            "VALUES (NULL, 'competitor', 'csv_upload', 'success', ?, 1)",
            (f"file-{i:02d}.csv",),
        )
    conn.commit()


def _open_history(conn):
    conn.close()
    at = AppTest.from_file(MAIN_SCRIPT, default_timeout=30)
    at.run()
    at.switch_page("screens/upload.py")
    at.run()
    return at


def test_history_shows_first_twenty(conn):
    _seed_loads(conn, 35)
    at = _open_history(conn)

    assert not list(at.exception)
    assert len(_history_rows(at)) == 20
    assert any("Показано 20 из 35" in c.value for c in at.caption)
    assert any(b.label.startswith("Показать ещё") for b in at.button)


def test_history_loads_ten_more_per_click(conn):
    _seed_loads(conn, 35)
    at = _open_history(conn)

    more = next(b for b in at.button if b.label.startswith("Показать ещё"))
    assert more.label == "Показать ещё 10"
    more.click()
    at.run()
    assert len(_history_rows(at)) == 30
    assert any("Показано 30 из 35" in c.value for c in at.caption)

    # на последнем шаге остаток меньше шага — кнопка говорит честную цифру
    more = next(b for b in at.button if b.label.startswith("Показать ещё"))
    assert more.label == "Показать ещё 5"
    more.click()
    at.run()
    assert len(_history_rows(at)) == 35
    assert any("Показано 35 из 35" in c.value for c in at.caption)


def test_history_button_disappears_when_everything_shown(conn):
    _seed_loads(conn, 35)
    at = _open_history(conn)

    for _ in range(2):
        next(b for b in at.button if b.label.startswith("Показать ещё")).click()
        at.run()

    assert not any(b.label.startswith("Показать ещё") for b in at.button)
    # вместо неё появляется «Свернуть»
    collapse = next(b for b in at.button if b.label == "Свернуть")
    collapse.click()
    at.run()
    assert len(_history_rows(at)) == 20


def test_history_without_button_when_few_loads(conn):
    _seed_loads(conn, 7)
    at = _open_history(conn)

    assert len(_history_rows(at)) == 7
    assert any("Показано 7 из 7" in c.value for c in at.caption)
    assert not any(b.label.startswith("Показать ещё") for b in at.button)
    assert not any(b.label == "Свернуть" for b in at.button)


def test_history_empty_state(conn):
    at = _open_history(conn)
    assert any("Загрузок пока не было" in i.value for i in at.info)


def test_import_sends_telegram_alert_for_critical_deviation(conn):
    """Прайс конкурента дешевле на 30% при пороге 15% — уведомление уходит."""
    from app.services import catalog as catalog_service
    from app.services import settings as settings_service
    from app.services import telegram

    catalog_service.create_product(conn, "COF-1", "Кофе Арабика 250г", 100.0)
    settings_service.set_setting(conn, "telegram_bot_token", "123:abc")
    settings_service.set_setting(conn, "telegram_chat_id", "777")
    conn.commit()
    conn.close()

    at = AppTest.from_file(MAIN_SCRIPT, default_timeout=30)
    at.run()
    at.switch_page("screens/upload.py")
    at.run()

    at.file_uploader[0].set_value(
        ("k1.csv", "sku,цена\nCOF-1,70.00\n".encode("utf-8"), "text/csv")
    )
    at.run()

    sent = {}

    def fake_send(cfg, text):
        sent["chat_id"] = cfg.chat_id
        sent["text"] = text

    import app.services.telegram as telegram_module

    telegram_module.send_message = fake_send
    try:
        next(b for b in at.button if b.label.startswith("Запустить импорт")).click()
        at.run()
    finally:
        telegram_module.send_message = telegram.send_message

    assert not list(at.exception)
    assert sent.get("chat_id") == "777"
    assert "COF-1" in sent.get("text", "")
    assert any("Уведомление в Telegram отправлено" in i.value for i in at.info)


def test_import_without_telegram_configured_shows_nothing_extra(conn):
    from app.services import catalog as catalog_service

    catalog_service.create_product(conn, "COF-1", "Кофе Арабика 250г", 100.0)
    conn.close()

    at = AppTest.from_file(MAIN_SCRIPT, default_timeout=30)
    at.run()
    at.switch_page("screens/upload.py")
    at.run()

    at.file_uploader[0].set_value(
        ("k1.csv", "sku,цена\nCOF-1,70.00\n".encode("utf-8"), "text/csv")
    )
    at.run()
    next(b for b in at.button if b.label.startswith("Запустить импорт")).click()
    at.run()

    assert not list(at.exception)
    assert not any("Telegram" in i.value for i in at.info)
    assert not any("Telegram" in w.value for w in at.warning)


def test_import_reports_telegram_send_failure_without_failing_import(conn):
    from app.services import catalog as catalog_service
    from app.services import settings as settings_service
    from app.services import telegram

    catalog_service.create_product(conn, "COF-1", "Кофе Арабика 250г", 100.0)
    settings_service.set_setting(conn, "telegram_bot_token", "123:abc")
    settings_service.set_setting(conn, "telegram_chat_id", "777")
    conn.commit()
    conn.close()

    at = AppTest.from_file(MAIN_SCRIPT, default_timeout=30)
    at.run()
    at.switch_page("screens/upload.py")
    at.run()

    at.file_uploader[0].set_value(
        ("k1.csv", "sku,цена\nCOF-1,70.00\n".encode("utf-8"), "text/csv")
    )
    at.run()

    import app.services.telegram as telegram_module

    def boom(cfg, text):
        raise telegram.TelegramError("HTTP 403. бот заблокирован получателем")

    telegram_module.send_message = boom
    try:
        next(b for b in at.button if b.label.startswith("Запустить импорт")).click()
        at.run()
    finally:
        telegram_module.send_message = telegram.send_message

    assert not list(at.exception)
    # импорт всё равно считается успешным — предупреждение только про отправку
    assert any("Импорт завершён" in s.value for s in at.get("success"))
    assert any("Импорт сохранён" in w.value and "не отправлено" in w.value for w in at.warning)
    assert any("бот заблокирован" in w.value for w in at.warning)


def test_file_date_column_wins_over_snapshot_picker(conn):
    """Дата из колонки файла не должна подменяться виджетом «Дата цен (снимок)».

    Раньше price_date_override передавался в run_import безусловно — виджет
    всегда показывал сегодняшнюю дату по умолчанию, и она перекрывала дату
    из файла. Если для этого SKU в этом контуре уже была цена с датой
    «сегодня» (обычное дело при повторных загрузках в один день), новая
    цена с другой датой из файла молча помечалась дублем и терялась.
    """
    from app.services import catalog as catalog_service

    catalog_service.create_product(conn, "COF-1", "Кофе Арабика 250г", 300.0)
    conn.close()

    at = AppTest.from_file(MAIN_SCRIPT, default_timeout=30)
    at.run()
    at.switch_page("screens/upload.py")
    at.run()

    at.file_uploader[0].set_value(
        ("k1.csv", "sku,цена,дата\nCOF-1,299.90,2026-09-01\n".encode("utf-8"), "text/csv")
    )
    at.run()

    date_widget = next(d for d in at.date_input if d.label == "Дата цен (снимок)")
    assert date_widget.disabled is True
    assert any("берётся из неё построчно" in c.value for c in at.caption)

    next(b for b in at.button if b.label.startswith("Запустить импорт")).click()
    at.run()
    assert not list(at.exception)

    conn2 = db_module.init_db()
    try:
        row = conn2.execute("SELECT price_date FROM prices").fetchone()
        assert row["price_date"] == "2026-09-01"
    finally:
        conn2.close()


def test_snapshot_picker_active_without_date_column(conn):
    """Без колонки даты в файле виджет остаётся рабочим фолбэком."""
    from app.services import catalog as catalog_service

    catalog_service.create_product(conn, "COF-1", "Кофе Арабика 250г", 300.0)
    conn.close()

    at = AppTest.from_file(MAIN_SCRIPT, default_timeout=30)
    at.run()
    at.switch_page("screens/upload.py")
    at.run()

    at.file_uploader[0].set_value(
        ("k1.csv", "sku,цена\nCOF-1,299.90\n".encode("utf-8"), "text/csv")
    )
    at.run()

    date_widget = next(d for d in at.date_input if d.label == "Дата цен (снимок)")
    assert date_widget.disabled is False

    date_widget.set_value(__import__("datetime").date(2026, 5, 1))
    at.run()
    next(b for b in at.button if b.label.startswith("Запустить импорт")).click()
    at.run()
    assert not list(at.exception)

    conn2 = db_module.init_db()
    try:
        row = conn2.execute("SELECT price_date FROM prices").fetchone()
        assert row["price_date"] == "2026-05-01"
    finally:
        conn2.close()


def test_new_date_in_file_not_dropped_as_duplicate_of_same_day_row(conn):
    """Сквозной сценарий бага: цена конкурента с другой датой из файла не
    должна теряться из-за совпадения с уже загруженной ценой «на сегодня»."""
    from app.services import catalog as catalog_service
    from app.services import loads as loads_service

    catalog_service.create_product(conn, "COF-1", "Кофе Арабика 250г", 400.0)
    # уже есть цена, загруженная ранее «на сегодня» (без даты в файле)
    loads_service.run_import(
        conn, "sku,цена\nCOF-1,399.90\n".encode("utf-8"), "old.csv", "competitor"
    )
    conn.commit()
    conn.close()

    at = AppTest.from_file(MAIN_SCRIPT, default_timeout=30)
    at.run()
    at.switch_page("screens/upload.py")
    at.run()

    at.file_uploader[0].set_value(
        ("new.csv", "sku,цена,дата\nCOF-1,340.00,2026-09-22\n".encode("utf-8"), "text/csv")
    )
    at.run()
    next(b for b in at.button if b.label.startswith("Запустить импорт")).click()
    at.run()

    assert not list(at.exception)
    assert any("Импорт завершён" in s.value for s in at.get("success"))

    conn2 = db_module.init_db()
    try:
        prices = conn2.execute(
            "SELECT price, price_date FROM prices ORDER BY id"
        ).fetchall()
        assert len(prices) == 2
        assert prices[1]["price"] == 340.0
        assert prices[1]["price_date"] == "2026-09-22"
    finally:
        conn2.close()
