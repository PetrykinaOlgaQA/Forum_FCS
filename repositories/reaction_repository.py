from sqlalchemy import text

from .base import BaseRepository


class ReactionRepository(BaseRepository):
    def count_likes(self, post_id: int) -> int:
        return self.conn.execute(
            text(
                """
                SELECT COUNT(*) FROM post_reactions
                WHERE post_id = :pid AND reaction = 1
                """
            ),
            {"pid": post_id},
        ).scalar() or 0

    def user_has_like(self, user_id: int, post_id: int) -> bool:
        row = self.conn.execute(
            text(
                """
                SELECT 1 FROM post_reactions
                WHERE user_id = :uid AND post_id = :pid AND reaction = 1
                """
            ),
            {"uid": user_id, "pid": post_id},
        ).fetchone()
        return row is not None

    def toggle_like(self, user_id: int, post_id: int) -> bool:
        """
        Возвращает True, если после вызова лайк стоит; False — снят.
        """
        if self.user_has_like(user_id, post_id):
            self.conn.execute(
                text(
                    "DELETE FROM post_reactions WHERE user_id = :uid AND post_id = :pid"
                ),
                {"uid": user_id, "pid": post_id},
            )
            return False
        self.conn.execute(
            text(
                """
                INSERT INTO post_reactions (user_id, post_id, reaction)
                VALUES (:uid, :pid, 1)
                ON CONFLICT (user_id, post_id) DO UPDATE SET reaction = 1
                """
            ),
            {"uid": user_id, "pid": post_id},
        )
        return True
