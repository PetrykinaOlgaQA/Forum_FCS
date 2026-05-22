from sqlalchemy import text

from .base import BaseRepository


class UserRepository(BaseRepository):
    """Доступ к таблице users: регистрация, вход, баны на контент."""

    def get_by_email(self, email: str):
        """Возвращает строку пользователя по email или None."""
        return self.conn.execute(
            text("SELECT id, username, email, password, role FROM users WHERE email = :email"),
            {"email": email},
        ).fetchone()

    def create(self, username: str, email: str, password_hash: str):
        """Создаёт нового пользователя с хэшем пароля."""
        self.conn.execute(
            text("INSERT INTO users (username, email, password) VALUES (:username, :email, :password)"),
            {"username": username, "email": email, "password": password_hash},
        )

    def update_password(self, user_id: int, password_hash: str):
        """Обновляет пароль (например после миграции с открытого текста на хэш)."""
        self.conn.execute(
            text("UPDATE users SET password = :p WHERE id = :id"),
            {"p": password_hash, "id": user_id},
        )

    def get_by_id(self, user_id: int):
        """Возвращает пользователя по первичному ключу."""
        return self.conn.execute(
            text("SELECT id, username, email, password, role FROM users WHERE id = :id"),
            {"id": user_id},
        ).fetchone()

    def exists_by_email_or_username(self, email: str, username: str) -> bool:
        """True, если email или username уже заняты."""
        return (
            self.conn.execute(
                text("SELECT 1 FROM users WHERE email = :email OR username = :username"),
                {"email": email, "username": username},
            ).fetchone()
            is not None
        )

    def get_all(self):
        """Список username и email для админ-страницы."""
        return self.conn.execute(text("SELECT username, email FROM users")).fetchall()

    def get_content_ban_until(self, user_id: int):
        """Момент окончания бана на публикации или None."""
        return self.conn.execute(
            text("SELECT content_ban_until FROM users WHERE id = :id"),
            {"id": user_id},
        ).scalar()

    def set_content_ban_hours(self, user_id: int, hours: float = 1.0):
        """Продлевает или устанавливает бан на посты и комментарии на указанное число часов."""
        self.conn.execute(
            text(
                """
                UPDATE users
                SET content_ban_until = GREATEST(
                    COALESCE(content_ban_until, '-infinity'::timestamptz),
                    NOW() + (interval '1 hour' * CAST(:hours AS double precision))
                )
                WHERE id = :id
                """
            ),
            {"id": user_id, "hours": hours},
        )

    def list_active_content_bans(self):
        """Пользователи с действующим content_ban_until."""
        return self.conn.execute(
            text(
                """
                SELECT id, username, email, content_ban_until
                FROM users
                WHERE content_ban_until IS NOT NULL AND content_ban_until > NOW()
                ORDER BY content_ban_until DESC
                """
            )
        ).fetchall()

    def clear_content_ban(self, user_id: int):
        """Снимает ограничение на создание контента."""
        self.conn.execute(
            text("UPDATE users SET content_ban_until = NULL WHERE id = :id"),
            {"id": user_id},
        )
