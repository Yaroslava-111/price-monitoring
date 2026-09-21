"""Агент в live-режиме: вызов модели, откат на шаблон, запись режима в журнал.

Сеть здесь не используется — подменяется `llm.chat`.
"""

import json

import pytest

from app.services import agent as agent_service
from app.services import catalog as catalog_service
from app.services import llm
from app.services import matching as matching_service
from app.services import settings as settings_service
from app.storage import db as db_module

LIVE = {
    "agent_endpoint": "https://agent.example/v1",
    "agent_key": "secret-key",
    "agent_model": "test-model",
}


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    monkeypatch.setattr(db_module, "_db_path", lambda: tmp_path / "test.db")
    connection = db_module.init_db()
    yield connection
    connection.close()


def _configure(conn, **overrides):
    values = dict(LIVE)
    values.update(overrides)
    for key, value in values.items():
        settings_service.set_setting(conn, key, value)
    conn.commit()


def _seed_competitor(conn):
    """Товар за 100 ₽ и цена конкурента 70 ₽ — отклонение -30%."""
    product = catalog_service.create_product(
        conn, "SKU-1", "Кофе Арабика 250 г", 100.0, category="Кофе"
    )
    load_id = conn.execute(
        "INSERT INTO loads (source_id, scope, kind, status) "
        "VALUES (NULL, 'competitor', 'csv_upload', 'success')"
    ).lastrowid
    matching_service.upsert_mapping(
        conn,
        product_id=product.id,
        external_key="SKU-1",
        external_name="Кофе Арабика 250 г",
        method="exact",
        similarity=100.0,
        status="confirmed",
    )
    conn.execute(
        "INSERT INTO prices (product_id, source_id, load_id, price, price_date, external_key) "
        "VALUES (?, NULL, ?, 70.0, '2026-09-10', 'SKU-1')",
        (product.id, load_id),
    )
    conn.commit()
    return product


def _log(conn):
    return conn.execute("SELECT * FROM agent_log ORDER BY id").fetchall()


def _payloads(conn):
    return [
        json.loads(r["payload"])
        for r in conn.execute("SELECT payload FROM recommendations ORDER BY id")
    ]


# ---- режим ----


def test_mock_mode_when_not_configured(conn):
    adapter = agent_service.load_agent(conn)
    assert adapter.is_live is False
    assert adapter.mode() == "mock"


def test_mock_mode_when_model_missing(conn):
    """Частичная настройка — всё ещё заглушка. Именно этот случай был у владельца."""
    _configure(conn, agent_model="")
    adapter = agent_service.load_agent(conn)
    assert adapter.is_live is False
    assert "модель" in adapter.config.missing()


def test_live_mode_when_fully_configured(conn):
    _configure(conn)
    adapter = agent_service.load_agent(conn)
    assert adapter.is_live is True
    assert adapter.mode() == "live"


# ---- проверка подключения ----


def test_check_connection_success(conn, monkeypatch):
    _configure(conn)
    monkeypatch.setattr(llm, "chat", lambda *a, **k: "готов")

    adapter = agent_service.load_agent(conn)
    assert adapter.check_connection() == "готов"

    record = _log(conn)[-1]
    assert record["task"] == "check_connection"
    assert record["status"] == "success"


def test_check_connection_failure_is_logged(conn, monkeypatch):
    _configure(conn)

    def boom(*a, **k):
        raise llm.LLMError("HTTP 401. ключ не принят")

    monkeypatch.setattr(llm, "chat", boom)

    adapter = agent_service.load_agent(conn)
    with pytest.raises(agent_service.AgentError) as exc:
        adapter.check_connection()
    assert "401" in str(exc.value)

    record = _log(conn)[-1]
    assert record["status"] == "failed"
    assert "401" in record["error"]


def test_check_connection_refuses_when_not_configured(conn):
    adapter = agent_service.load_agent(conn)
    with pytest.raises(agent_service.AgentError) as exc:
        adapter.check_connection()
    assert "не настроен" in str(exc.value)


# ---- обоснования от модели ----


def test_live_analysis_uses_model_text(conn, monkeypatch):
    product = _seed_competitor(conn)
    _configure(conn)

    sent = {}

    def fake_chat(config, messages, **kwargs):
        sent["prompt"] = messages[-1]["content"]
        return json.dumps(
            [{"product_id": product.id, "rationale": "Текст от модели."}],
            ensure_ascii=False,
        )

    monkeypatch.setattr(llm, "chat", fake_chat)

    recs = agent_service.load_agent(conn).analyze_prices("competitor")
    assert [r["rationale"] for r in recs] == ["Текст от модели."]

    # модель получает уже посчитанные цифры, а не сырые строки базы
    assert "deviation_pct" in sent["prompt"]
    assert "secret-key" not in sent["prompt"]

    payload = _payloads(conn)[0]
    assert payload["mode"] == "live"
    # расчёт остаётся локальным и не зависит от текста модели
    assert payload["target_price"] == 70.0
    assert payload["deviation_pct"] == -30.0


def test_falls_back_to_template_when_model_fails(conn, monkeypatch):
    _seed_competitor(conn)
    _configure(conn)

    def boom(*a, **k):
        raise llm.LLMError("Сервер не ответил за 60 с.")

    monkeypatch.setattr(llm, "chat", boom)

    recs = agent_service.load_agent(conn).analyze_prices("competitor")
    assert len(recs) == 1
    assert "Конкурент" in recs[0]["rationale"]
    assert _payloads(conn)[0]["mode"] == "mock"

    record = _log(conn)[-1]
    assert record["status"] == "success"
    assert "live→mock" in record["result"]
    assert "не ответил" in record["error"]


def test_falls_back_when_model_returns_garbage(conn, monkeypatch):
    _seed_competitor(conn)
    _configure(conn)
    monkeypatch.setattr(llm, "chat", lambda *a, **k: "Извините, не могу помочь.")

    recs = agent_service.load_agent(conn).analyze_prices("competitor")
    assert "Конкурент" in recs[0]["rationale"]
    assert "live→mock" in _log(conn)[-1]["result"]


def test_mock_mode_never_calls_model(conn, monkeypatch):
    _seed_competitor(conn)

    def boom(*a, **k):
        raise AssertionError("в режиме-заглушке сеть трогать нельзя")

    monkeypatch.setattr(llm, "chat", boom)

    recs = agent_service.load_agent(conn).analyze_prices("competitor")
    assert len(recs) == 1
    assert _log(conn)[-1]["result"].startswith("mock:")


# ---- разбор ответа модели ----


@pytest.mark.parametrize(
    "answer",
    [
        '[{"product_id": 7, "rationale": "Текст"}]',
        'Вот результат:\n[{"product_id": 7, "rationale": "Текст"}]\nГотово.',
        '```json\n[{"product_id": 7, "rationale": "Текст"}]\n```',
        '[{"product_id": "7", "rationale": "Текст"}]',
    ],
)
def test_parse_rationales_tolerates_wrapping(answer):
    assert agent_service._parse_rationales(answer) == {7: "Текст"}


@pytest.mark.parametrize(
    "answer",
    ["", "не могу", "{}", "[]", '[{"rationale": "без id"}]', '[{"product_id": 7}]'],
)
def test_parse_rationales_rejects_bad_shapes(answer):
    assert agent_service._parse_rationales(answer) == {}


def test_parse_rationales_truncates_long_text():
    answer = json.dumps([{"product_id": 1, "rationale": "я" * 500}])
    assert len(agent_service._parse_rationales(answer)[1]) == 300
