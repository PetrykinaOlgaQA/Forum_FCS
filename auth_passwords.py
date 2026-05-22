from __future__ import annotations

from werkzeug.security import check_password_hash, generate_password_hash


def hash_password(plain: str) -> str:
    """Возвращает безопасный хэш пароля для сохранения в БД."""
    return generate_password_hash(plain)


def is_hashed(stored: str | None) -> bool:
    """Проверяет, что в БД уже лежит хэш, а не открытый текст."""
    if not stored:
        return False
    return stored.startswith("pbkdf2:") or stored.startswith("scrypt:")


def verify_password(stored: str | None, plain: str) -> bool:
    """Сравнивает введённый пароль с хэшем или с устаревшим открытым значением в БД."""
    if not stored or plain is None:
        return False
    if is_hashed(stored):
        return check_password_hash(stored, plain)
    return stored == plain
