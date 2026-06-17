from __future__ import annotations

import re
from typing import Iterable

_WS_RE = re.compile(r"\s+", flags=re.UNICODE)
_NON_WORD_RE = re.compile(r"[^\w\u0400-\u04FF]+", flags=re.UNICODE)


def normalize_text(text: str | None) -> str:
    if not text:
        return ""
    s = str(text).lower().replace("ё", "е")
    s = _NON_WORD_RE.sub(" ", s)
    s = _WS_RE.sub(" ", s).strip()
    return s


def _strip_ru_suffix(word: str) -> str:
    """
    Очень лёгкий стеммер для русских слов: срезает частые окончания.
    Это НЕ полноценный морфо-анализатор, но даёт "корни" для TF-IDF.
    """
    w = word
    if len(w) < 4:
        return w

    # типовые частицы/постфиксы
    for suf in ("ся", "сь"):
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            w = w[: -len(suf)]
            break

    # частые окончания (существительные/прилагательные/глаголы)
    suffixes: tuple[str, ...] = (
        "иями",
        "ями",
        "ами",
        "иями",
        "ыми",
        "ими",
        "его",
        "ого",
        "ему",
        "ому",
        "ее",
        "ое",
        "ая",
        "яя",
        "ое",
        "ые",
        "ий",
        "ый",
        "ой",
        "ам",
        "ям",
        "ах",
        "ях",
        "ом",
        "ем",
        "ою",
        "ею",
        "у",
        "ю",
        "а",
        "я",
        "о",
        "е",
        "ы",
        "и",
        "ь",
    )

    for suf in suffixes:
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            return w[: -len(suf)]
    return w


def _strip_en_suffix(word: str) -> str:
    # очень упрощённый английский стемминг (без зависимостей)
    w = word
    if len(w) < 4:
        return w
    for suf in ("ing", "edly", "edly", "edly", "ed", "ly", "es", "s"):
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            return w[: -len(suf)]
    return w


def stem_token(word: str) -> str:
    w = word.strip().lower().replace("ё", "е")
    if not w:
        return ""
    # русский/кириллица
    if re.search(r"[\u0400-\u04FF]", w):
        return _strip_ru_suffix(w)
    return _strip_en_suffix(w)


def stem_tokenize(text: str | None) -> list[str]:
    s = normalize_text(text)
    if not s:
        return []
    out: list[str] = []
    for raw in s.split(" "):
        if not raw:
            continue
        t = stem_token(raw)
        if t and len(t) >= 2:
            out.append(t)
    return out


def stem_join(text: str | None) -> str:
    """Иногда удобно хранить "текст из корней" как строку."""
    return " ".join(stem_tokenize(text))


def stem_join_many(texts: Iterable[str | None]) -> list[str]:
    return [stem_join(t) for t in texts]

