from .base import BaseRepository
from sqlalchemy import text

class TopicRepository(BaseRepository):
    def get_by_title(self, title: str):
        return self.conn.execute(
            text("SELECT id FROM topics WHERE title = :title"), {"title": title}
        ).fetchone()

    def create(self, title: str, description: str, user_id: int):
        result = self.conn.execute(
            text(
                """
                INSERT INTO topics (title, description, user_id)
                VALUES (:title, :description, :user_id) RETURNING id
                """
            ),
            {"title": title, "description": description, "user_id": user_id},
        )
        row = result.fetchone()
        if row:
            try:
                return row.id if hasattr(row, "id") else row[0]
            except (AttributeError, IndexError):
                return row[0] if len(row) > 0 else None
        return None

    def get_all(self):
        return self.conn.execute(text("SELECT id, title FROM topics")).fetchall()

    def list_pairs(self):
        return self.conn.execute(
            text(
                """
                SELECT t1.id AS left_id, t1.title AS left_title,
                       t2.id AS right_id, t2.title AS right_title
                FROM topics t1
                JOIN topics t2 ON t2.id = t1.paired_topic_id
                ORDER BY t1.title
                """
            )
        ).fetchall()