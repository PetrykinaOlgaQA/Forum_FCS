from .base import BaseRepository
from sqlalchemy import text

class UserRepository(BaseRepository):
    def get_by_email(self, email: str):
        return self.conn.execute(
            text(
                "SELECT id, username, email, password, role FROM users WHERE email = :email"
            ),
            {"email": email},
        ).fetchone()

    def create(self, username: str, email: str, password: str):
        self.conn.execute(
            text(
                "INSERT INTO users (username, email, password) VALUES (:username, :email, :password)"
            ),
            {"username": username, "email": email, "password": password},
        )

    def exists_by_email_or_username(self, email: str, username: str):
        return (
            self.conn.execute(
                text(
                    "SELECT 1 FROM users WHERE email = :email OR username = :username"
                ),
                {"email": email, "username": username},
            ).fetchone()
            is not None
        )

    def get_all(self):
        return self.conn.execute(text("SELECT username, email FROM users")).fetchall()