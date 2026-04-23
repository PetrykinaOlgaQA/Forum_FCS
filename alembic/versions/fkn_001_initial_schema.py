"""Начальная схема ФКН (users, topics, posts, comments, post_reactions).

Revision ID: fkn_001_initial
Revises:
Create Date: 2026-04-05

"""
from pathlib import Path
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from db.sql_split import split_sql

revision: str = "fkn_001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ROOT = Path(__file__).resolve().parents[2]
_SCHEMA = _ROOT / "db" / "schema.sql"


def upgrade() -> None:
    raw = _SCHEMA.read_text(encoding="utf-8")
    for chunk in split_sql(raw):
        op.execute(sa.text(chunk))


def downgrade() -> None:
    op.execute(sa.text("DROP TABLE IF EXISTS post_reactions CASCADE"))
    op.execute(sa.text("DROP TABLE IF EXISTS comments CASCADE"))
    op.execute(sa.text("DROP TABLE IF EXISTS posts CASCADE"))
    op.execute(sa.text("DROP TABLE IF EXISTS topics CASCADE"))
    op.execute(sa.text("DROP TABLE IF EXISTS users CASCADE"))
