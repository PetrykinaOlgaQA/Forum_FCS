"""
Создание БД и применение db/schema.sql (с нуля).
Использование: задать DB_* в окружении или скопировать .env.example в .env.
"""
import os
import pathlib

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL

from db.sql_split import split_sql


def load_dotenv(path: str = ".env"):
    env_path = pathlib.Path(path)
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


load_dotenv()

DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "home1213")
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "forum_bd")

ROOT = pathlib.Path(__file__).resolve().parent
SCHEMA_PATH = ROOT / "db" / "schema.sql"


def main():
    admin_url = URL.create(
        "postgresql+pg8000",
        username=DB_USER,
        password=DB_PASSWORD,
        host=DB_HOST,
        port=int(DB_PORT),
        database="postgres",
    )
    engine_admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with engine_admin.connect() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": DB_NAME}
        ).scalar()
        if not exists:
            if not DB_NAME.replace("_", "").isalnum() or not DB_NAME[0].isalpha():
                raise ValueError("DB_NAME: только буквы, цифры и _, начало с буквы")
            conn.execute(text(f"CREATE DATABASE {DB_NAME}"))
            print(f"База «{DB_NAME}» создана.")
        else:
            print(f"База «{DB_NAME}» уже существует.")

    db_url = URL.create(
        "postgresql+pg8000",
        username=DB_USER,
        password=DB_PASSWORD,
        host=DB_HOST,
        port=int(DB_PORT),
        database=DB_NAME,
    )
    engine = create_engine(db_url, isolation_level="AUTOCOMMIT")
    raw = SCHEMA_PATH.read_text(encoding="utf-8")
    with engine.connect() as conn:
        reg = conn.execute(text("SELECT to_regclass('public.users')")).scalar()
        if reg:
            print(
                "Таблица public.users уже есть — schema.sql не применялся. "
                "Для обновления старой БД выполните db/migrate_v2.sql, migrate_v3.sql и migrate_v4.sql."
            )
            return
        for chunk in split_sql(raw):
            conn.execute(text(chunk))
    print(f"Схема применена из {SCHEMA_PATH}")
    print("Зафиксируйте миграции Alembic (чтобы не накатывать схему повторно):")
    print("  alembic stamp fkn_001_initial")


if __name__ == "__main__":
    main()
