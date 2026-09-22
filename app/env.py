"""Минимальная загрузка .env без внешних зависимостей.

Streamlit не читает .env сам, поэтому поднимаем ключи в os.environ
небольшим парсером на стандартной библиотеке (ничего устанавливать не нужно).
Уже заданные в системе переменные не перетираются.
"""

from __future__ import annotations

import os
from pathlib import Path

ENV_FILE = Path(__file__).resolve().parents[1] / ".env"


def load_env_file(path: str | Path = ENV_FILE) -> None:
    """Читает .env и кладёт ключи в os.environ, если их там ещё нет.

    Поддерживается простой синтаксис KEY=VALUE, комментарии (#) и пустые
    строки. Значения могут быть в кавычках.
    """
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


load_env_file()