"""Клиент Telegram Bot API для критических уведомлений о ценах.

Как и `llm.py`, работает на `urllib` из стандартной библиотеки — новый
пакет ради одного POST-запроса не нужен. Токен живёт только в памяти и в
локальной базе (открытым текстом, как и ключ ИИ-агента); наружу, в текст
ошибок, не попадает — только код ответа и описание от самого Telegram.
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass

TIMEOUT = 15.0

TEST_MESSAGE = "Проверка связи: бот подключён к приложению «Ценовой мониторинг»."

_urlopen = urllib.request.urlopen


class TelegramError(RuntimeError):
    """Ошибка отправки в Telegram, пригодная для показа владельцу."""


@dataclass(frozen=True)
class TelegramConfig:
    bot_token: str = ""
    chat_id: str = ""

    @property
    def configured(self) -> bool:
        return bool(self.bot_token.strip() and self.chat_id.strip())

    def missing(self) -> list[str]:
        gaps = []
        if not self.bot_token.strip():
            gaps.append("токен бота")
        if not self.chat_id.strip():
            gaps.append("chat_id")
        return gaps


def _api_url(token: str, method: str) -> str:
    return f"https://api.telegram.org/bot{token.strip()}/{method}"


def _decode_body(raw: str) -> dict:
    try:
        payload = json.loads(raw)
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _explain_http_error(exc: urllib.error.HTTPError) -> str:
    try:
        raw = exc.read().decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001 — тело ошибки не обязано читаться
        raw = ""
    description = _decode_body(raw).get("description")

    hints = {
        400: "запрос отклонён — проверьте chat_id",
        401: "токен бота не принят",
        403: "бот заблокирован получателем или удалён из чата/канала",
        404: "неверный токен бота",
        429: "слишком много сообщений, попробуйте позже",
    }
    hint = hints.get(exc.code, "")
    parts = [f"HTTP {exc.code}"]
    if hint:
        parts.append(hint)
    if description:
        parts.append(f"ответ Telegram: {str(description)[:300]}")
    return ". ".join(parts)


def send_message(config: TelegramConfig, text: str, *, timeout: float = TIMEOUT) -> None:
    """Отправляет текст в чат. Молчит при успехе, иначе бросает TelegramError."""
    if not config.configured:
        raise TelegramError(
            "Telegram не настроен: не заданы " + ", ".join(config.missing()) + "."
        )

    body = json.dumps(
        {
            "chat_id": config.chat_id.strip(),
            "text": text,
            "disable_web_page_preview": True,
        }
    ).encode("utf-8")

    request = urllib.request.Request(
        _api_url(config.bot_token, "sendMessage"),
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )

    try:
        with _urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        raise TelegramError(_explain_http_error(exc)) from exc
    except socket.timeout as exc:
        raise TelegramError(f"Telegram не ответил за {timeout:.0f} с.") from exc
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        if isinstance(reason, socket.timeout):
            raise TelegramError(f"Telegram не ответил за {timeout:.0f} с.") from exc
        raise TelegramError(f"Нет связи с Telegram: {reason}") from exc
    except OSError as exc:  # noqa: BLE001 — сеть может упасть и мимо URLError
        raise TelegramError(f"Сбой сети: {exc}") from exc

    payload = _decode_body(raw)
    if payload.get("ok") is not True:
        description = payload.get("description") or "Telegram вернул ошибку без описания."
        raise TelegramError(str(description)[:300])


def send_test(config: TelegramConfig) -> None:
    """Проверочное сообщение — используется кнопкой в Настройках."""
    send_message(config, TEST_MESSAGE)
