"""Клиент к OpenAI-совместимому API (Timeweb Cloud AI).

Namеренно на стандартной библиотеке: проекту не нужен лишний пакет ради
одного POST-запроса. Транспорт вынесен в модульную переменную `_urlopen`,
чтобы тесты подменяли его и не ходили в сеть.

Ключ живёт только в памяти и уходит единственным заголовком Authorization.
Ни в логи, ни в текст ошибок он не попадает.
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass

DEFAULT_TIMEOUT = 60.0
PING_TIMEOUT = 20.0

_urlopen = urllib.request.urlopen


class LLMError(RuntimeError):
    """Ошибка обращения к модели, пригодная для показа владельцу."""


@dataclass(frozen=True)
class LLMConfig:
    endpoint: str = ""
    api_key: str = ""
    model: str = ""

    @property
    def configured(self) -> bool:
        return bool(self.endpoint.strip() and self.api_key.strip() and self.model.strip())

    def missing(self) -> list[str]:
        gaps = []
        if not self.endpoint.strip():
            gaps.append("эндпоинт")
        if not self.api_key.strip():
            gaps.append("API-ключ")
        if not self.model.strip():
            gaps.append("модель")
        return gaps


def chat_url(endpoint: str) -> str:
    """Собирает адрес /chat/completions из базового эндпоинта."""
    base = endpoint.strip().rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    return f"{base}/chat/completions"


def _decode_error_body(exc: urllib.error.HTTPError) -> str:
    try:
        raw = exc.read().decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001 — тело ошибки не обязано читаться
        return ""
    raw = raw.strip()
    if not raw:
        return ""
    try:
        payload = json.loads(raw)
    except ValueError:
        return raw[:300]
    message = payload.get("error")
    if isinstance(message, dict):
        message = message.get("message")
    return str(message or raw)[:300]


def _explain_http_error(exc: urllib.error.HTTPError) -> str:
    detail = _decode_error_body(exc)
    hints = {
        401: "ключ не принят — проверьте API-ключ",
        403: (
            "доступ закрыт — агент остановлен в панели Timeweb "
            "либо у ключа нет прав на него"
        ),
        404: "адрес не найден — проверьте эндпоинт",
        422: "сервер не понял запрос — вероятно, неверное имя модели",
        429: "слишком много запросов, попробуйте позже",
        500: "сбой на стороне сервера, попробуйте позже",
        502: "сервер недоступен, попробуйте позже",
        503: "сервис временно недоступен, попробуйте позже",
    }
    hint = hints.get(exc.code, "")
    parts = [f"HTTP {exc.code}"]
    if hint:
        parts.append(hint)
    if detail:
        parts.append(f"ответ сервера: {detail}")
    return ". ".join(parts)


def chat(
    config: LLMConfig,
    messages: list[dict],
    *,
    temperature: float = 0.2,
    max_tokens: int = 900,
    timeout: float = DEFAULT_TIMEOUT,
) -> str:
    """Один запрос к модели. Возвращает текст ответа."""
    if not config.configured:
        raise LLMError("Агент не настроен: не заданы " + ", ".join(config.missing()) + ".")

    body = json.dumps(
        {
            "model": config.model.strip(),
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
    ).encode("utf-8")

    request = urllib.request.Request(
        chat_url(config.endpoint),
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {config.api_key.strip()}",
        },
    )

    try:
        with _urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        raise LLMError(_explain_http_error(exc)) from exc
    except socket.timeout as exc:
        raise LLMError(f"Сервер не ответил за {timeout:.0f} с.") from exc
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        if isinstance(reason, socket.timeout):
            raise LLMError(f"Сервер не ответил за {timeout:.0f} с.") from exc
        raise LLMError(f"Нет связи с сервером: {reason}") from exc
    except OSError as exc:  # noqa: BLE001 — сеть может упасть и мимо URLError
        raise LLMError(f"Сбой сети: {exc}") from exc

    return _extract_message(raw)


def _extract_message(raw: str) -> str:
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        raise LLMError("Сервер вернул не JSON.") from exc

    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        error = payload.get("error")
        if error:
            message = error.get("message") if isinstance(error, dict) else error
            raise LLMError(f"Сервер вернул ошибку: {str(message)[:300]}")
        raise LLMError("В ответе сервера нет поля choices.")

    message = choices[0].get("message") or {}
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise LLMError("Модель вернула пустой ответ.")
    return content.strip()


def ping(config: LLMConfig) -> str:
    """Короткий проверочный запрос. Возвращает ответ модели или бросает LLMError."""
    return chat(
        config,
        [
            {"role": "system", "content": "Отвечай одним словом."},
            {"role": "user", "content": "Ответь словом: готов"},
        ],
        max_tokens=16,
        timeout=PING_TIMEOUT,
    )
