-- Журнал действий администратора с указанием причины

CREATE TABLE IF NOT EXISTS moderation_log (
    id SERIAL PRIMARY KEY,
    actor_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    action VARCHAR(64) NOT NULL,
    target_type VARCHAR(32) NOT NULL,
    target_id INTEGER NOT NULL,
    reason TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_moderation_log_created ON moderation_log(created_at DESC);
