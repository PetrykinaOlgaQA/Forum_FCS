from sqlalchemy import text

from .base import BaseRepository


class CommentRepository(BaseRepository):
    def get_by_post_id(self, post_id: int):
        return self.conn.execute(
            text(
                """
            SELECT c.id,
                   CASE WHEN c.deleted_by_moderator
                        THEN '[Сообщение удалено модератором]'
                        ELSE c.content END AS content,
                   c.created_date, u.username AS author, c.user_id,
                   c.deleted_by_moderator
            FROM comments c
            JOIN users u ON c.user_id = u.id
            WHERE c.post_id = :id
            ORDER BY c.created_date ASC
        """
            ),
            {"id": post_id},
        ).fetchall()

    def create(self, post_id: int, user_id: int, content: str):
        self.conn.execute(
            text(
                """
                INSERT INTO comments (post_id, user_id, content)
                VALUES (:post_id, :user_id, :content)
                """
            ),
            {"post_id": post_id, "user_id": user_id, "content": content},
        )

    def update(self, comment_id: int, content: str):
        self.conn.execute(
            text(
                """
                UPDATE comments SET content = :content, deleted_by_moderator = FALSE
                WHERE id = :id
                """
            ),
            {"content": content, "id": comment_id},
        )

    def delete(self, comment_id: int):
        self.conn.execute(text("DELETE FROM comments WHERE id = :id"), {"id": comment_id})

    def mark_deleted_by_moderator(self, comment_id: int):
        self.conn.execute(
            text(
                """
                UPDATE comments
                SET content = '[Сообщение удалено модератором]',
                    deleted_by_moderator = TRUE
                WHERE id = :id
                """
            ),
            {"id": comment_id},
        )

    def get_by_id(self, comment_id: int):
        return self.conn.execute(
            text(
                """
            SELECT c.id, c.content, c.created_date, c.user_id, c.post_id, c.deleted_by_moderator
            FROM comments c
            WHERE c.id = :id
        """
            ),
            {"id": comment_id},
        ).fetchone()

    def get_by_user_id(self, user_id: int):
        return self.conn.execute(
            text(
                """
            SELECT c.id, c.content, c.created_date, c.post_id
            FROM comments c
            WHERE c.user_id = :user_id
            ORDER BY c.created_date DESC
        """
            ),
            {"user_id": user_id},
        ).fetchall()
