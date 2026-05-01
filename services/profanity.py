"""Обнаружение нецензурной лексики (рус.) для модерации контента."""
from __future__ import annotations

import re

# Корни и слова для фильтра (обучающий проект; список не исчерпывающий).
_BAD_SUBSTRINGS = (
    "хуй",
    "хуе",
    "хуя",
    "хуи",
    "пизд",
    "ебан",
    "ебат",
    "ебал",
    "ебло",
    "ебен",
    "ёбан",
    "ёбат",
    "бляд",
    "блят",
    "сука",
    "сукин",
    "мудак",
    "мудил",
    "гандон",
    "пидор",
    "пидр",
    "заеб",
    "уеб",
    "уёб",
    "хер",
    "залуп",
)


def _normalize(text: str) -> str:
    if not text:
        return ""
    s = text.lower().replace("ё", "е")
    s = re.sub(r"[^\w\u0400-\u04FF]+", " ", s, flags=re.UNICODE)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def contains_profanity(text: str | None) -> bool:
    if not text or not str(text).strip():
        return False
    raw = text.lower().replace("ё", "е")
    collapsed = re.sub(r"[^\w\u0400-\u04FF]+", "", raw, flags=re.UNICODE)
    norm = _normalize(text)
    for w in _BAD_SUBSTRINGS:
        if w in raw or w in collapsed or w in norm:
            return True
    return False
