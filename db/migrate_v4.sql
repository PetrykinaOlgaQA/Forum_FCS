-- Бан на публикации и комментарии, вложенность комментариев (без точки с запятой в тексте комментария — иначе ломается split миграций).

ALTER TABLE users ADD COLUMN IF NOT EXISTS content_ban_until TIMESTAMPTZ;

ALTER TABLE comments ADD COLUMN IF NOT EXISTS parent_id INTEGER REFERENCES comments(id) ON DELETE CASCADE;

CREATE INDEX IF NOT EXISTS idx_comments_parent ON comments(parent_id);
