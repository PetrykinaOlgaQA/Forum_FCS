-- Роль admin, увеличение поля пароля, полнотекстовые индексы GIN (конфигурация simple).

ALTER TABLE users ALTER COLUMN password TYPE VARCHAR(512);

ALTER TABLE users DROP CONSTRAINT IF EXISTS users_role_check;
ALTER TABLE users ADD CONSTRAINT users_role_check CHECK (role IN ('user', 'moderator', 'admin'));

CREATE INDEX IF NOT EXISTS idx_posts_fts ON posts USING GIN (to_tsvector('simple', coalesce(content, '')));
CREATE INDEX IF NOT EXISTS idx_topics_fts ON topics USING GIN (to_tsvector('simple', coalesce(title, '')));
CREATE INDEX IF NOT EXISTS idx_users_fts ON users USING GIN (to_tsvector('simple', coalesce(username, '')));
