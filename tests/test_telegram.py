"""Клиент Telegram Bot API. Сеть подменена — реальных запросов нет."""

import io
import json
import socket
import urllib.error

import pytest

from app.services import telegram

CONFIG = telegram.TelegramConfig(bot_token="123:secret-token", chat_id="42")


class _FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def _ok_body():
    return json.dumps({"ok": True, "result": {"message_id": 1}}).encode("utf-8")


@pytest.fixture()
def calls(monkeypatch):
    recorded = {}

    def fake_urlopen(request, timeout=None):
        recorded["url"] = request.full_url
        recorded["timeout"] = timeout
        recorded["body"] = json.loads(request.data.decode("utf-8"))
        return _FakeResponse(recorded.get("response", _ok_body()))

    monkeypatch.setattr(telegram, "_urlopen", fake_urlopen)
    return recorded


def _raise(exc):
    def _fake(request, timeout=None):
        raise exc

    return _fake


# ---- конфигурация ----


def test_configured_requires_both_fields():
    assert CONFIG.configured
    assert telegram.TelegramConfig("", "42").missing() == ["токен бота"]
    assert telegram.TelegramConfig("123:t", "").missing() == ["chat_id"]
    assert not telegram.TelegramConfig().configured


def test_send_refuses_without_config():
    with pytest.raises(telegram.TelegramError) as exc:
        telegram.send_message(telegram.TelegramConfig(), "текст")
    assert "токен бота" in str(exc.value)
    assert "chat_id" in str(exc.value)


# ---- успешный путь ----


def test_send_message_hits_correct_url_and_body(calls):
    telegram.send_message(CONFIG, "Критическое отклонение")

    assert calls["url"] == "https://api.telegram.org/bot123:secret-token/sendMessage"
    assert calls["body"]["chat_id"] == "42"
    assert calls["body"]["text"] == "Критическое отклонение"


def test_send_test_uses_fixed_message(calls):
    telegram.send_test(CONFIG)
    assert calls["body"]["text"] == telegram.TEST_MESSAGE


# ---- ok:false в теле ответа (Telegram часто отвечает 200 даже на ошибку) ----


def test_ok_false_in_200_response_is_an_error(calls):
    calls["response"] = json.dumps(
        {"ok": False, "error_code": 400, "description": "chat not found"}
    ).encode("utf-8")
    with pytest.raises(telegram.TelegramError) as exc:
        telegram.send_message(CONFIG, "текст")
    assert "chat not found" in str(exc.value)


def test_non_json_response_treated_as_failure(calls):
    calls["response"] = b"not json"
    with pytest.raises(telegram.TelegramError):
        telegram.send_message(CONFIG, "текст")


# ---- HTTP-ошибки ----


def test_http_401_explains_token(monkeypatch):
    err = urllib.error.HTTPError(
        "https://a", 401, "Unauthorized", {},
        io.BytesIO(json.dumps({"description": "Unauthorized"}).encode("utf-8")),
    )
    monkeypatch.setattr(telegram, "_urlopen", _raise(err))
    with pytest.raises(telegram.TelegramError) as exc:
        telegram.send_message(CONFIG, "текст")
    text = str(exc.value)
    assert "401" in text
    assert "токен бота не принят" in text


def test_http_403_mentions_blocked_bot(monkeypatch):
    err = urllib.error.HTTPError(
        "https://a", 403, "Forbidden", {},
        io.BytesIO(json.dumps({"description": "bot was blocked"}).encode("utf-8")),
    )
    monkeypatch.setattr(telegram, "_urlopen", _raise(err))
    with pytest.raises(telegram.TelegramError) as exc:
        telegram.send_message(CONFIG, "текст")
    assert "заблокирован" in str(exc.value)
    assert "bot was blocked" in str(exc.value)


def test_timeout_is_reported(monkeypatch):
    monkeypatch.setattr(telegram, "_urlopen", _raise(socket.timeout()))
    with pytest.raises(telegram.TelegramError) as exc:
        telegram.send_message(CONFIG, "текст", timeout=5)
    assert "не ответил" in str(exc.value)


def test_network_failure_is_reported(monkeypatch):
    monkeypatch.setattr(telegram, "_urlopen", _raise(urllib.error.URLError("недоступно")))
    with pytest.raises(telegram.TelegramError) as exc:
        telegram.send_message(CONFIG, "текст")
    assert "Нет связи" in str(exc.value)


def test_token_never_leaks_into_error(monkeypatch):
    err = urllib.error.HTTPError("https://a", 401, "Unauthorized", {}, io.BytesIO(b"denied"))
    monkeypatch.setattr(telegram, "_urlopen", _raise(err))
    with pytest.raises(telegram.TelegramError) as exc:
        telegram.send_message(CONFIG, "текст")
    assert "secret-token" not in str(exc.value)
