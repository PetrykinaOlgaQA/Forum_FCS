"""Разбор db/schema.sql на отдельные выражения для выполнения через SQLAlchemy/Alembic."""


def split_sql(sql: str) -> list[str]:
    parts: list[str] = []
    buf: list[str] = []
    for line in sql.splitlines():
        s = line.strip()
        if s.startswith("--") or not s:
            continue
        buf.append(line)
        if s.endswith(";"):
            stmt = "\n".join(buf).strip().rstrip(";").strip()
            buf = []
            if stmt:
                parts.append(stmt)
    return parts
