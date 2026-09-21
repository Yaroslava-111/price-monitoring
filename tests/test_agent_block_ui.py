"""Блок агента на экранах: итог запроса виден, список рекомендаций выводится."""

import json

import pytest
from streamlit.testing.v1 import AppTest

from app.services import catalog as catalog_service
from app.services import matching as matching_service
from app.storage import db as db_module
from app.ui import agent_block

MAIN_SCRIPT = str(db_module.BASE_DIR / "app" / "main.py")


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    monkeypatch.setattr(db_module, "_db_path", lambda: tmp_path / "test.db")
    connection = db_module.init_db()
    yield connection
    connection.close()


def _seed_competitor(conn):
    """Товар за 100 ₽ и цена конкурента 70 ₽ — агенту есть что предложить."""
    product = catalog_service.create_product(
        conn, "COF-1", "Кофе Арабика 250 г", 100.0, category="Кофе"
    )
    load_id = conn.execute(
        "INSERT INTO loads (source_id, scope, kind, status) "
        "VALUES (NULL, 'competitor', 'csv_upload', 'success')"
    ).lastrowid
    matching_service.upsert_mapping(
        conn,
        product_id=product.id,
        external_key="COF-1",
        external_name="Кофе Арабика 250 г",
        method="exact",
        similarity=100.0,
        status="confirmed",
    )
    conn.execute(
        "INSERT INTO prices (product_id, source_id, load_id, price, price_date, external_key) "
        "VALUES (?, NULL, ?, 70.0, '2026-09-10', 'COF-1')",
        (product.id, load_id),
    )
    conn.commit()
    return product


def _open_report(conn):
    conn.close()
    at = AppTest.from_file(MAIN_SCRIPT, default_timeout=30)
    at.run()
    at.switch_page("screens/report.py")
    at.run()
    return at


def _agent_button(at):
    return next(b for b in at.button if "рекомендации агента" in b.label)


def _texts(at, kind):
    return [e.value for e in at.get(kind)]


# ---- итог запроса виден ----


def test_first_request_reports_new_recommendations(conn):
    """Раньше st.rerun() в finally стирал сообщение — кнопка выглядела мёртвой."""
    _seed_competitor(conn)
    at = _open_report(conn)

    assert _texts(at, "success") == []
    _agent_button(at).click()
    at.run()

    assert not list(at.exception)
    success = _texts(at, "success")
    assert any("новых рекомендаций — 1" in s for s in success), success


def test_result_message_is_shown_once(conn):
    """Сообщение живёт ровно одну отрисовку и не залипает на экране."""
    _seed_competitor(conn)
    at = _open_report(conn)

    _agent_button(at).click()
    at.run()
    assert _texts(at, "success")

    at.run()
    assert _texts(at, "success") == []


def test_repeat_request_says_nothing_new(conn):
    _seed_competitor(conn)
    at = _open_report(conn)

    _agent_button(at).click()
    at.run()
    _agent_button(at).click()
    at.run()

    success = _texts(at, "success")
    assert any("Новых рекомендаций нет" in s for s in success), success


def test_nothing_to_recommend_is_explained(conn):
    """Без подходящих данных агент объясняет причину, а не молчит.

    Проверяется на уровне блока: сам экран «Конкуренты» при пустой выборке
    обрывается раньше и кнопку агента не рисует.
    """
    import streamlit as st

    from app.services.agent import load_agent

    catalog_service.create_product(conn, "COF-1", "Кофе", 100.0)
    conn.commit()

    st.session_state.clear()
    agent_block._run(conn, load_agent(conn), "competitor", "price_change")
    result = st.session_state[agent_block.RESULT_KEY]

    assert result["level"] == "info"
    assert "нечего предложить" in result["text"]
    assert "конкурент дешевле" in result["text"]


# ---- список рекомендаций ----


def test_recommendation_list_appears_with_counts(conn):
    _seed_competitor(conn)
    at = _open_report(conn)

    _agent_button(at).click()
    at.run()

    assert any("Рекомендации агента (1)" in h.value for h in at.subheader)
    labels = [e.label for e in at.get("expander")]
    assert any("целевая цена 70.00 ₽" in label for label in labels), labels


def test_supplier_screen_also_lists_recommendations(conn):
    """На поставщиках список раньше не выводился вообще."""
    product = catalog_service.create_product(
        conn, "MIL-1", "Молоко 1 л", 90.0, category="Молочка"
    )
    load_id = conn.execute(
        "INSERT INTO loads (source_id, scope, kind, status) "
        "VALUES (NULL, 'supplier', 'csv_upload', 'success')"
    ).lastrowid
    matching_service.upsert_mapping(
        conn,
        product_id=product.id,
        external_key="MIL-1",
        external_name="Молоко 1 л",
        method="exact",
        similarity=100.0,
        status="confirmed",
    )
    conn.execute(
        "INSERT INTO prices (product_id, source_id, load_id, price, price_date, external_key) "
        "VALUES (?, NULL, ?, 38.0, '2026-09-10', 'MIL-1')",
        (product.id, load_id),
    )
    conn.commit()
    conn.close()

    at = AppTest.from_file(MAIN_SCRIPT, default_timeout=30)
    at.run()
    at.switch_page("screens/suppliers.py")
    at.run()

    _agent_button(at).click()
    at.run()

    assert not list(at.exception)
    assert any("Рекомендации агента" in h.value for h in at.subheader)


# ---- оформление строк ----


def test_money_formatting_keeps_thousands_separator():
    assert agent_block._money(1234.5) == "1 234.50 ₽"
    assert agent_block._money(70) == "70.00 ₽"


def test_title_does_not_mangle_commas_in_names():
    """Замена разделителя тысяч не должна портить название поставщика."""
    row = {"sku": "MIL-1", "product_name": "Молоко, 1 л"}
    payload = {"supplier": "Оптбаза, ООО", "supplier_price": 1500.0}
    title = agent_block._title(row, payload)
    assert "Молоко, 1 л" in title
    assert "Оптбаза, ООО" in title
    assert "1 500.00 ₽" in title
    assert "  " not in title


def test_facts_line_shows_mode_and_deviation():
    facts = agent_block._facts(
        {"deviation_pct": -12.5, "competitor": "К1", "mode": "mock"}
    )
    assert "-12.5%" in facts
    assert "К1" in facts
    assert "текст по шаблону" in facts

    facts_live = agent_block._facts({"mode": "live"})
    assert "написан моделью" in facts_live


def test_broken_payload_is_skipped(conn):
    """Битый JSON в рекомендации не должен ронять экран."""
    product = catalog_service.create_product(conn, "X-1", "Товар", 100.0)
    conn.execute(
        "INSERT INTO recommendations (product_id, rec_type, payload, status) "
        "VALUES (?, 'price_change', '{не json', 'proposed')",
        (product.id,),
    )
    conn.execute(
        "INSERT INTO recommendations (product_id, rec_type, payload, status) "
        "VALUES (?, 'price_change', ?, 'proposed')",
        (product.id, json.dumps({"rationale": "Живая"}, ensure_ascii=False)),
    )
    conn.commit()

    recs = agent_block._list_recommendations(conn, "price_change")
    assert [r["rationale"] for r in recs] == ["Живая"]
