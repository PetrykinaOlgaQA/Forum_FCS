"""Хэширование паролей (Werkzeug). Совместимость со старыми открытыми паролями в БД."""
from __future__ import annotations

from werkzeug.security import check_password_hash, generate_password_hash


def hash_password(plain: str) -> str:
    return generate_password_hash(plain)


def is_hashed(stored: str | None) -> bool:
    if not stored:
        return False
    return stored.startswith("pbkdf2:") or stored.startswith("scrypt:")


def verify_password(stored: str | None, plain: str) -> bool:
    if not stored or plain is None:
        return False
    if is_hashed(stored):
        return check_password_hash(stored, plain)
    return stored == plain
