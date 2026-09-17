-- 仅保存调用元数据，不保存问题、回答、密钥或 Cookie。
CREATE TABLE IF NOT EXISTS model_calls (
    request_id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    model TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    lease_expires_at INTEGER NOT NULL,
    finished_at INTEGER,
    duration_ms INTEGER,
    state TEXT NOT NULL CHECK (state IN ('running', 'succeeded', 'failed', 'timed_out', 'cancelled', 'abandoned')),
    input_chars INTEGER NOT NULL,
    output_chars INTEGER,
    input_tokens INTEGER,
    output_tokens INTEGER,
    error_code TEXT
);
CREATE INDEX IF NOT EXISTS model_calls_user_time ON model_calls(user_id, created_at);
CREATE INDEX IF NOT EXISTS model_calls_time ON model_calls(created_at);
CREATE INDEX IF NOT EXISTS model_calls_running ON model_calls(state, lease_expires_at, user_id);
