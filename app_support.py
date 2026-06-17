"""Инфраструктура приложения: БД, миграции, работа со строками SQLAlchemy, дерево комментариев."""
from __future__ import annotations

import os
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL

from auth_passwords import hash_password, is_hashed

DEMO_ADMIN_EMAIL = "admin@fkn.vsu.ru"
DEMO_ADMIN_USERNAME = "admin_fkn"
DEMO_ADMIN_PASSWORD = "AdminFkn2026!"

ALLOWED_UPLOAD_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
MAX_UPLOAD_BYTES = 5 * 1024 * 1024


def load_dotenv(path: str = ".env") -> None:
    """Читает .env и подставляет переменные в os.environ (без перезаписи уже заданных)."""
    env_path = Path(path)
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def build_database_url() -> str | URL:
    """Собирает URL PostgreSQL из DATABASE_URL или отдельных переменных DB_*."""
    url = os.getenv("DATABASE_URL")
    if url:
        return url
    return URL.create(
        "postgresql+pg8000",
        username=os.getenv("DB_USER", "postgres"),
        password=os.getenv("DB_PASSWORD", "home1213"),
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", "5432")),
        database=os.getenv("DB_NAME", "forum_bd"),
    )


def create_db_engine():
    """Создаёт пул соединений SQLAlchemy с проверкой живости перед выдачей."""
    return create_engine(build_database_url(), echo=False, pool_pre_ping=True)


def split_migration_sql(raw_sql: str) -> list[str]:
    """Разбивает SQL-файл миграции на отдельные команды, отбрасывая ведущие строки-комментарии."""
    statements: list[str] = []
    for raw_chunk in raw_sql.split(";"):
        chunk = raw_chunk.strip()
        if not chunk:
            continue
        lines = chunk.splitlines()
        while lines and lines[0].strip().startswith("--"):
            lines.pop(0)
        stmt = "\n".join(lines).strip()
        if stmt:
            statements.append(stmt)
    return statements


def run_sql_migrations(engine) -> None:
    """По очереди применяет db/migrate_v2.sql … migrate_v6.sql к существующей схеме (ошибки отдельных команд игнорируются)."""
    root = Path(__file__).resolve().parent / "db"
    for fname in ("migrate_v2.sql", "migrate_v3.sql", "migrate_v4.sql", "migrate_v5.sql", "migrate_v6.sql"):
        path = root / fname
        if not path.exists():
            continue
        statements = split_migration_sql(path.read_text(encoding="utf-8"))
        if not statements:
            continue
        try:
            with engine.begin() as conn:
                if not conn.execute(text("SELECT to_regclass('public.users')")).scalar():
                    return
                for statement in statements:
                    try:
                        conn.execute(text(statement))
                    except Exception:
                        continue
        except Exception:
            continue


def ensure_demo_admin(engine) -> None:
    """Гарантирует учётную запись демо-администратора с ролью admin."""
    try:
        with engine.begin() as conn:
            row = conn.execute(
                text("SELECT id, role FROM users WHERE email = :e"),
                {"e": DEMO_ADMIN_EMAIL},
            ).fetchone()
            if row:
                uid = row_get(row, "id", 0)
                if row_get(row, "role", 1) != "admin":
                    conn.execute(
                        text("UPDATE users SET role = 'admin' WHERE id = :id"),
                        {"id": uid},
                    )
                return
            conn.execute(
                text(
                    "INSERT INTO users (username, email, password, role) "
                    "VALUES (:u, :e, :p, 'admin')"
                ),
                {
                    "u": DEMO_ADMIN_USERNAME,
                    "e": DEMO_ADMIN_EMAIL,
                    "p": hash_password(DEMO_ADMIN_PASSWORD),
                },
            )
    except Exception:
        pass


def row_get(row, name: str, index: int | None = None):
    """Возвращает поле строки результата SQL (по имени или позиции)."""
    if row is None:
        return None
    if hasattr(row, name):
        return getattr(row, name)
    mapping = getattr(row, "_mapping", None)
    if mapping is not None and name in mapping:
        return mapping[name]
    if index is not None:
        try:
            return row[index]
        except (IndexError, TypeError):
            return None
    return None


def session_user_from_row(row) -> dict | None:
    """Формирует словарь пользователя для Flask-сессии из строки таблицы users."""
    if row is None:
        return None
    password = row_get(row, "password", 3)
    user_id = row_get(row, "id", 0)
    username = row_get(row, "username", 1)
    role = row_get(row, "role", 4) or "user"
    if user_id is None or username is None:
        return None
    return {"id": user_id, "name": username, "role": role, "_password": password}


def uploads_goods_dir() -> Path:
    """Каталог для загрузки фотографий товаров (создаётся при отсутствии)."""
    directory = Path(__file__).resolve().parent / "static" / "uploads" / "goods"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def content_ban_active(user_repo, user_id: int) -> datetime | None:
    """Возвращает момент окончания бана на публикации или None, если бан не действует."""
    until = user_repo.get_content_ban_until(user_id)
    if until is None:
        return None
    if getattr(until, "tzinfo", None) is None:
        until = until.replace(tzinfo=timezone.utc)
    if until > datetime.now(timezone.utc):
        return until
    return None


def mute_until_for_user(engine, user_id: int) -> datetime | None:
    """Читает content_ban_until из БД для отображения в шаблонах."""
    try:
        with engine.connect() as conn:
            raw = conn.execute(
                text("SELECT content_ban_until FROM users WHERE id = :id"),
                {"id": user_id},
            ).scalar()
        if raw is None:
            return None
        mt = raw if getattr(raw, "tzinfo", None) else raw.replace(tzinfo=timezone.utc)
        if mt > datetime.now(timezone.utc):
            return mt
        return None
    except Exception:
        return None


def log_moderation(conn, actor_id: int, action: str, target_type: str, target_id: int, reason: str) -> None:
    """Пишет запись в moderation_log (если таблица есть)."""
    try:
        conn.execute(
            text(
                """
                INSERT INTO moderation_log (actor_id, action, target_type, target_id, reason)
                VALUES (:a, :ac, :tt, :ti, :r)
                """
            ),
            {
                "a": actor_id,
                "ac": action[:64],
                "tt": target_type[:32],
                "ti": target_id,
                "r": (reason or "")[:4000],
            },
        )
    except Exception:
        pass


def comment_parent_id(row) -> int | None:
    """Извлекает parent_id комментария из строки выборки."""
    return row_get(row, "parent_id", 6)


def comment_row_id(row) -> int:
    """Извлекает id комментария из строки выборки."""
    return row_get(row, "id", 0)


def build_comment_tree(rows) -> list[tuple]:
    """Строит плоский список (строка комментария, глубина) в порядке обхода дерева ответов."""
    children: dict[int, list] = defaultdict(list)
    for row in rows:
        children[comment_parent_id(row) or 0].append(row)

    def sort_key(r):
        return row_get(r, "created_date", 3)

    result: list[tuple] = []

    def walk(parent_key: int, depth: int) -> None:
        for node in sorted(children.get(parent_key, []), key=sort_key):
            result.append((node, depth))
            walk(comment_row_id(node), depth + 1)

    walk(0, 0)
    return result


def connection_error_message(exc, db_name: str, db_host: str, db_port: str, db_user: str) -> str:
    """Формирует понятное сообщение при ошибке подключения к PostgreSQL."""
    error_str = str(exc)
    if "3D000" in error_str or "database" in error_str.lower() or "не существует" in error_str:
        return (
            f"База данных '{db_name}' не существует.\n\n"
            f"Для создания базы данных выполните:\n"
            f"python create_database.py\n\n"
            f"Или подключитесь к PostgreSQL и выполните:\n"
            f"CREATE DATABASE {db_name};"
        )
    return (
        f"Не удалось подключиться к базе данных PostgreSQL.\n"
        f"Проверьте:\n"
        f"1. Запущен ли сервер PostgreSQL на {db_host}:{db_port}\n"
        f"2. Существует ли база данных '{db_name}'\n"
        f"3. Правильны ли учетные данные (пользователь: {db_user})\n"
        f"4. Доступен ли сервер из сети\n"
        f"5. Заполнен ли файл .env (DB_USER/DB_PASSWORD/DB_NAME)\n\n"
        f"Ошибка: {error_str}"
    )
