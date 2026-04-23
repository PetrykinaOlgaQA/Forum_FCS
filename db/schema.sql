-- Тематическая платформа ФКН ВГУ: пользователи, темы (в т.ч. пары «тема–тема»),
-- посты (обсуждение / товар / услуга), реакции, комментарии с модерацией.

CREATE TABLE users (
    id              SERIAL PRIMARY KEY,
    username        VARCHAR(50) NOT NULL UNIQUE,
    email           VARCHAR(255) NOT NULL UNIQUE,
    password        VARCHAR(255) NOT NULL,
    role            VARCHAR(20) NOT NULL DEFAULT 'user'
        CHECK (role IN ('user', 'moderator')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE topics (
    id                SERIAL PRIMARY KEY,
    title             VARCHAR(200) NOT NULL UNIQUE,
    description       TEXT NOT NULL DEFAULT '',
    user_id           INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    paired_topic_id   INTEGER REFERENCES topics(id) ON DELETE SET NULL
);

CREATE INDEX idx_topics_paired ON topics(paired_topic_id);

CREATE TABLE posts (
    id            SERIAL PRIMARY KEY,
    topic_id      INTEGER NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
    user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    content       TEXT NOT NULL,
    post_type     VARCHAR(20) NOT NULL DEFAULT 'post'
        CHECK (post_type IN ('post', 'good', 'service')),
    meta          JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_date  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_posts_topic ON posts(topic_id);
CREATE INDEX idx_posts_created ON posts(created_date DESC);
CREATE INDEX idx_posts_type ON posts(post_type);
CREATE INDEX idx_posts_meta_gin ON posts USING GIN (meta jsonb_path_ops);

CREATE TABLE comments (
    id                    SERIAL PRIMARY KEY,
    post_id               INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    user_id               INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    content               TEXT NOT NULL,
    created_date          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_by_moderator  BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE INDEX idx_comments_post ON comments(post_id);

CREATE TABLE post_reactions (
    user_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    post_id   INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    reaction  SMALLINT NOT NULL DEFAULT 1 CHECK (reaction IN (1, -1)),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, post_id)
);

CREATE INDEX idx_post_reactions_post ON post_reactions(post_id);

COMMENT ON TABLE topics IS 'Тематические разделы; paired_topic_id — связка «тема–пара» для перекрёстных обсуждений.';
COMMENT ON COLUMN posts.post_type IS 'post — обсуждение, good — объявление о продаже, service — услуга.';
COMMENT ON COLUMN posts.meta IS 'Доп. поля: price_rub, contact, tags (JSON).';
COMMENT ON COLUMN comments.deleted_by_moderator IS 'Мягкое удаление комментария модератором.';
