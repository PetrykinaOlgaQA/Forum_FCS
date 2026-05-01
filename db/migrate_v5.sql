-- Повторно и безопасно если migrate_v4 не применился из-за старого разбора SQL по точке с запятой
ALTER TABLE users ADD COLUMN IF NOT EXISTS content_ban_until TIMESTAMPTZ;
ALTER TABLE comments ADD COLUMN IF NOT EXISTS parent_id INTEGER REFERENCES comments(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS idx_comments_parent ON comments(parent_id);
