from sqlalchemy import text


class BaseRepository:
    """Базовый класс репозитория с общим соединением SQLAlchemy."""

    def __init__(self, conn):
        """Сохраняет активное соединение для выполнения SQL в рамках одной транзакции."""
        self.conn = conn
