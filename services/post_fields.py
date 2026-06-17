from __future__ import annotations

import re
from urllib.parse import urlparse

_MAX_PRICE = 99_999_999
_TG_RE = re.compile(r"^@?[a-zA-Z][a-zA-Z0-9_]{4,31}$")


def parse_price_rub(raw: str | None) -> tuple[int | None, str | None]:
    """Парсит цену в рублях: (число, None) или (None, текст ошибки). Пустая строка — (None, None)."""
    if raw is None or not str(raw).strip():
        return None, None
    s = str(raw).strip().replace(" ", "").replace("\u00a0", "")
    if not s.isdigit():
        return None, "Цена должна быть целым неотрицательным числом (руб.)."
    n = int(s)
    if n < 0:
        return None, "Цена не может быть отрицательной."
    if n > _MAX_PRICE:
        return None, f"Цена слишком велика (максимум {_MAX_PRICE})."
    return n, None


def validate_telegram_nick(raw: str | None) -> tuple[str | None, str | None]:
    """Проверяет ник Telegram и нормализует к виду @nickname."""
    if raw is None or not str(raw).strip():
        return None, None
    s = str(raw).strip()
    if not _TG_RE.match(s):
        return (
            None,
            "Некорректный ник Telegram: укажите @nickname или nickname (латиница, цифры, _, 5–32 символа).",
        )
    return (s, None) if s.startswith("@") else ("@" + s, None)


def validate_http_url(raw: str | None) -> tuple[str | None, str | None]:
    """Проверяет HTTP/HTTPS ссылку для поля контакта."""
    if raw is None or not str(raw).strip():
        return None, None
    s = str(raw).strip()
    if len(s) > 2000:
        return None, "Ссылка слишком длинная."
    p = urlparse(s)
    if p.scheme not in ("http", "https") or not p.netloc:
        return None, "Ссылка должна начинаться с http:// или https:// и содержать адрес сайта."
    return s, None
