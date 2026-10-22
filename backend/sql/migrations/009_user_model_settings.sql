-- Account-owned credentials are encrypted using MODEL_CONFIG_SECRET, never plaintext.
CREATE TABLE IF NOT EXISTS user_model_settings (
    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    enabled INTEGER NOT NULL DEFAULT 0 CHECK(enabled IN (0,1)),
    base_url TEXT NOT NULL DEFAULT '',
    api_key_encrypted TEXT NOT NULL DEFAULT '',
    chat_model TEXT NOT NULL DEFAULT '',
    coding_model TEXT NOT NULL DEFAULT '',
    vision_model TEXT NOT NULL DEFAULT '',
    updated_at INTEGER NOT NULL
);
