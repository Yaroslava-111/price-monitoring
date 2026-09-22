"""Клиент к OpenAI-совместимому API. Сеть подменена — реальных запросов нет."""

import io
import json
import socket
import urllib.error

import pytest

from app.services import llm

CONFIG = llm.LLMConfig(
    endpoint="https://agent.example/api/v1/cloud-ai/agents/abc/v1",
    api_key="secret-key",
    model="test-model",
)


class _FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def _ok_body(text="готов"):
    return json.dumps({"choices": [{"message": {"content": text}}]}).encode("utf-8")


@pytest.fixture()
def calls(monkeypatch):
    """Перехватывает исходящий запрос и отдаёт заранее заданный ответ."""
    recorded = {}

    def fake_urlopen(request, timeout=None):
        recorded["url"] = request.full_url
        recorded["timeout"] = timeout
        recorded["headers"] = dict(request.headers)
        recorded["body"] = json.loads(request.data.decode("utf-8"))
        return _FakeResponse(recorded.get("response", _ok_body()))

    monkeypatch.setattr(llm, "_urlopen", fake_urlopen)
    return recorded


# ---- сборка адреса ----


@pytest.mark.parametrize(
    "endpoint,expected",
    [
        ("https://a.example/v1", "https://a.example/v1/chat/completions"),
        ("https://a.example/v1/", "https://a.example/v1/chat/completions"),
        (
            "https://a.example/v1/chat/completions",
            "https://a.example/v1/chat/completions",
        ),
        ("  https://a.example/v1  ", "https://a.example/v1/chat/completions"),
    ],
)
def test_chat_url(endpoint, expected):
    assert llm.chat_url(endpoint) == expected


# ---- конфигурация ----


def test_configured_requires_all_three():
    assert CONFIG.configured
    assert llm.LLMConfig("", "k", "m").missing() == ["эндпоинт"]
    assert llm.LLMConfig("e", "", "m").missing() == ["API-ключ"]
    assert llm.LLMConfig("e", "k", "").missing() == ["модель"]
    assert not llm.LLMConfig("e", "k", "").configured


def test_chat_refuses_without_config():
    with pytest.raises(llm.LLMError) as exc:
        llm.chat(llm.LLMConfig(), [{"role": "user", "content": "привет"}])
    assert "модель" in str(exc.value)


# ---- успешный путь ----


def test_chat_sends_expected_request(calls):
    answer = llm.chat(CONFIG, [{"role": "user", "content": "привет"}])

    assert answer == "готов"
    assert calls["url"] == "https://agent.example/api/v1/cloud-ai/agents/abc/v1/chat/completions"
    assert calls["body"]["model"] == "test-model"
    assert calls["body"]["messages"] == [{"role": "user", "content": "привет"}]
    # ключ уходит единственным заголовком и ровно в ожидаемом виде
    headers = {k.lower(): v for k, v in calls["headers"].items()}
    assert headers["authorization"] == "Bearer secret-key"


def test_chat_strips_whitespace_in_answer(calls):
    calls["response"] = _ok_body("  готов  ")
    assert llm.chat(CONFIG, [{"role": "user", "content": "x"}]) == "готов"


def test_ping_uses_short_timeout(calls):
    llm.ping(CONFIG)
    assert calls["timeout"] == llm.PING_TIMEOUT


# ---- ошибки ----


def _raise(exc):
    def _fake(request, timeout=None):
        raise exc

    return _fake


def test_http_401_explains_key(monkeypatch):
    err = urllib.error.HTTPError(
        "https://a", 401, "Unauthorized", {}, io.BytesIO(b'{"error":{"message":"bad token"}}')
    )
    monkeypatch.setattr(llm, "_urlopen", _raise(err))
    with pytest.raises(llm.LLMError) as exc:
        llm.chat(CONFIG, [{"role": "user", "content": "x"}])
    text = str(exc.value)
    assert "401" in text
    assert "ключ не принят" in text
    assert "bad token" in text


def test_http_403_mentions_suspended_agent(monkeypatch):
    """Агент остановлен в панели, а не проблема с ключом."""
    err = urllib.error.HTTPError(
        "https://a", 403, "Forbidden", {}, io.BytesIO("Agent suspended".encode("utf-8"))
    )
    monkeypatch.setattr(llm, "_urlopen", _raise(err))
    with pytest.raises(llm.LLMError) as exc:
        llm.chat(CONFIG, [{"role": "user", "content": "x"}])
    text = str(exc.value)
    assert "403" in text
    assert "остановлен" in text
    assert "Agent suspended" in text


def test_http_422_hints_at_model(monkeypatch):
    err = urllib.error.HTTPError("https://a", 422, "Unprocessable", {}, io.BytesIO(b""))
    monkeypatch.setattr(llm, "_urlopen", _raise(err))
    with pytest.raises(llm.LLMError) as exc:
        llm.chat(CONFIG, [{"role": "user", "content": "x"}])
    assert "модели" in str(exc.value)


def test_timeout_is_reported(monkeypatch):
    monkeypatch.setattr(llm, "_urlopen", _raise(socket.timeout()))
    with pytest.raises(llm.LLMError) as exc:
        llm.chat(CONFIG, [{"role": "user", "content": "x"}], timeout=5)
    assert "не ответил" in str(exc.value)


def test_network_failure_is_reported(monkeypatch):
    monkeypatch.setattr(llm, "_urlopen", _raise(urllib.error.URLError("сеть недоступна")))
    with pytest.raises(llm.LLMError) as exc:
        llm.chat(CONFIG, [{"role": "user", "content": "x"}])
    assert "Нет связи" in str(exc.value)


def test_non_json_answer(calls):
    calls["response"] = b"<html>502 Bad Gateway</html>"
    with pytest.raises(llm.LLMError) as exc:
        llm.chat(CONFIG, [{"role": "user", "content": "x"}])
    assert "не JSON" in str(exc.value)


def test_answer_without_choices(calls):
    calls["response"] = json.dumps({"error": {"message": "нет квоты"}}).encode("utf-8")
    with pytest.raises(llm.LLMError) as exc:
        llm.chat(CONFIG, [{"role": "user", "content": "x"}])
    assert "нет квоты" in str(exc.value)


def test_empty_answer(calls):
    calls["response"] = _ok_body("   ")
    with pytest.raises(llm.LLMError) as exc:
        llm.chat(CONFIG, [{"role": "user", "content": "x"}])
    assert "пустой ответ" in str(exc.value)


def test_api_key_never_leaks_into_error(monkeypatch):
    """Текст ошибки показывается владельцу — ключа в нём быть не должно."""
    err = urllib.error.HTTPError(
        "https://a", 401, "Unauthorized", {}, io.BytesIO(b"denied")
    )
    monkeypatch.setattr(llm, "_urlopen", _raise(err))
    with pytest.raises(llm.LLMError) as exc:
        llm.chat(CONFIG, [{"role": "user", "content": "x"}])
    assert "secret-key" not in str(exc.value)
