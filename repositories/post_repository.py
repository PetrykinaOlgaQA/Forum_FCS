import json
from typing import Any, Optional

from sqlalchemy import text

from .base import BaseRepository


class PostRepository(BaseRepository):
    def get_post(self, post_id: int):
        return self.conn.execute(
            text(
                """
            SELECT p.id, p.content, p.created_date, u.username AS author, t.title AS topic_title,
                   t.user_id AS topic_user_id, t.id AS topic_id, p.user_id,
                   p.post_type, p.meta,
                   (SELECT COUNT(*) FROM post_reactions pr
                    WHERE pr.post_id = p.id AND pr.reaction = 1) AS like_count
            FROM posts p
            JOIN users u ON p.user_id = u.id
            JOIN topics t ON p.topic_id = t.id
            WHERE p.id = :id
        """
            ),
            {"id": post_id},
        ).fetchone()

    def get_by_user_id(self, user_id: int):
        return self.conn.execute(
            text(
                """
            SELECT p.id, p.content, p.created_date, p.post_type
            FROM posts p
            WHERE p.user_id = :user_id
            ORDER BY p.created_date DESC
        """
            ),
            {"user_id": user_id},
        ).fetchall()

    def get_all(
        self,
        where_clause: str = "",
        params: dict = None,
        order_by: str = "",
        limit: int = 10,
        offset: int = 0,
    ):
        if params is None:
            params = {}
        base = """
            SELECT p.id, p.content, p.created_date, u.username AS author, t.title AS topic_title,
                   (SELECT COUNT(*) FROM comments c WHERE c.post_id = p.id) AS comment_count,
                   (SELECT COUNT(*) FROM post_reactions pr
                    WHERE pr.post_id = p.id AND pr.reaction = 1) AS like_count,
                   p.user_id, p.post_type, p.meta
            FROM posts p
            JOIN users u ON p.user_id = u.id
            JOIN topics t ON p.topic_id = t.id
        """
        query = f"{base} {where_clause} ORDER BY {order_by} LIMIT :limit OFFSET :offset"
        params = {**params, "limit": limit, "offset": offset}
        return self.conn.execute(text(query), params).fetchall()

    def count(self, where_clause: str = "", params: dict = None):
        if params is None:
            params = {}
        query = f"""
            SELECT COUNT(*) FROM (
                SELECT p.id FROM posts p
                JOIN users u ON p.user_id = u.id
                JOIN topics t ON p.topic_id = t.id
                {where_clause}
            ) AS total
        """
        return self.conn.execute(text(query), params).scalar()

    def create(
        self,
        topic_id: int,
        user_id: int,
        content: str,
        post_type: str = "post",
        meta: Optional[dict[str, Any]] = None,
    ):
        payload = json.dumps(meta or {}, ensure_ascii=False)
        self.conn.execute(
            text(
                """
                INSERT INTO posts (topic_id, user_id, content, post_type, meta)
                VALUES (:topic_id, :user_id, :content, :post_type, CAST(:meta AS jsonb))
                """
            ),
            {
                "topic_id": topic_id,
                "user_id": user_id,
                "content": content,
                "post_type": post_type,
                "meta": payload,
            },
        )

    def update(self, post_id: int, content: str):
        self.conn.execute(
            text("UPDATE posts SET content = :content WHERE id = :id"),
            {"content": content, "id": post_id},
        )

    def delete(self, post_id: int):
        self.conn.execute(text("DELETE FROM posts WHERE id = :id"), {"id": post_id})
