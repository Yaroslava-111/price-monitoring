from __future__ import annotations

import re
import unicodedata

SKU_UNIFY = {
    "Ё": "Е",
    "ё": "е",
}

_TRANS = str.maketrans(SKU_UNIFY)


def normalize_sku(sku: str) -> str:
    text = unicodedata.normalize("NFKC", sku or "")
    text = text.translate(_TRANS)
    text = re.sub(r"\s+", " ", text.strip())
    return text.upper()


def normalize_name(name: str) -> str:
    text = unicodedata.normalize("NFKC", name or "")
    text = text.translate(_TRANS)
    text = text.lower()
    text = re.sub(r"[^а-яa-z0-9]+", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text).strip()
    return text