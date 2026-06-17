from __future__ import annotations

import re

_BAD_SUBSTRINGS = (
    "хуй", "хуе", "хуя", "хуи", "пизд", "ебан", "ебат", "ебал", "ебло", "ебен",
    "ёбан", "ёбат", "бляд", "блят", "сука", "сукин", "мудак", "мудил", "гандон",
    "пидор", "пидр", "заеб", "уеб", "уёб", "хер", "залуп",
)


def _normalize(text: str) -> str:
    """Приводит текст к нижнему регистру и оставляет только буквы для поиска корней."""
    if not text:
        return ""
    s = text.lower().replace("ё", "е")
    s = re.sub(r"[^\w\u0400-\u04FF]+", " ", s, flags=re.UNICODE)
    return re.sub(r"\s+", " ", s).strip()


def contains_profanity(text: str | None) -> bool:
    """Возвращает True, если в тексте найдена подстрока из словаря нецензурных корней."""
    if not text or not str(text).strip():
        return False
    raw = text.lower().replace("ё", "е")
    collapsed = re.sub(r"[^\w\u0400-\u04FF]+", "", raw, flags=re.UNICODE)
    norm = _normalize(text)
    return any(w in raw or w in collapsed or w in norm for w in _BAD_SUBSTRINGS)
