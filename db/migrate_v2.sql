-- Обновление существующей схемы «форум» до тематической платформы ФКН.
-- Выполнять на базе, где уже есть таблицы users / topics / posts / comments (имена в нижнем регистре).

ALTER TABLE users ADD COLUMN IF NOT EXISTS role VARCHAR(20) DEFAULT 'user';
UPDATE users SET role = 'user' WHERE role IS NULL;
ALTER TABLE users ALTER COLUMN role SET NOT NULL;
ALTER TABLE users DROP CONSTRAINT IF EXISTS users_role_check;
ALTER TABLE users ADD CONSTRAINT users_role_check CHECK (role IN ('user', 'moderator'));

ALTER TABLE topics ADD COLUMN IF NOT EXISTS paired_topic_id INTEGER REFERENCES topics(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS idx_topics_paired ON topics(paired_topic_id);

ALTER TABLE posts ADD COLUMN IF NOT EXISTS post_type VARCHAR(20) DEFAULT 'post';
UPDATE posts SET post_type = 'post' WHERE post_type IS NULL;
ALTER TABLE posts ALTER COLUMN post_type SET NOT NULL;
ALTER TABLE posts DROP CONSTRAINT IF EXISTS posts_post_type_check;
ALTER TABLE posts ADD CONSTRAINT posts_post_type_check CHECK (post_type IN ('post', 'good', 'service'));

ALTER TABLE posts ADD COLUMN IF NOT EXISTS meta JSONB NOT NULL DEFAULT '{}'::jsonb;
CREATE INDEX IF NOT EXISTS idx_posts_type ON posts(post_type);
CREATE INDEX IF NOT EXISTS idx_posts_meta_gin ON posts USING GIN (meta jsonb_path_ops);

ALTER TABLE comments ADD COLUMN IF NOT EXISTS deleted_by_moderator BOOLEAN NOT NULL DEFAULT FALSE;

CREATE TABLE IF NOT EXISTS post_reactions (
    user_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    post_id   INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    reaction  SMALLINT NOT NULL DEFAULT 1 CHECK (reaction IN (1, -1)),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, post_id)
);
CREATE INDEX IF NOT EXISTS idx_post_reactions_post ON post_reactions(post_id);
