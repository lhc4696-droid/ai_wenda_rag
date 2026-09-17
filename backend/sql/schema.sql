-- 知言 AI：SQLite 账号与登录会话结构。
-- 所有时间字段为 UTC Unix 秒数。
-- 可重复执行；只创建缺失的表和索引，不清空已有账号。
-- CREATE TABLE IF NOT EXISTS 不会修改已有列，后续结构变更需单独编写迁移。

PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

-- 用户名不区分英文字母大小写；保存密码哈希，不保存明文密码。
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    email TEXT,
    email_verified_at INTEGER,
    auth_version INTEGER NOT NULL DEFAULT 0
);

CREATE UNIQUE INDEX IF NOT EXISTS users_email ON users(email) WHERE email IS NOT NULL;

-- 一个账号允许多个设备登录。数据库只存随机会话令牌的 SHA-256 摘要。
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS sessions_expiry ON sessions(expires_at);
CREATE INDEX IF NOT EXISTS sessions_user ON sessions(user_id);

-- 注册/登录限流计数。bucket 为 IP 或用户名对应的计数键。
CREATE TABLE IF NOT EXISTS auth_attempts (
    bucket TEXT PRIMARY KEY,
    count INTEGER NOT NULL,
    reset_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS auth_attempts_reset ON auth_attempts(reset_at);

-- 邮箱地址按应用统一规则规范化；验证码只保存带服务器密钥的 HMAC。
CREATE TABLE IF NOT EXISTS email_verifications (
    id TEXT PRIMARY KEY,
    email TEXT NOT NULL,
    purpose TEXT NOT NULL CHECK (purpose IN ('register', 'reset_password')),
    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    code_hash TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    status TEXT NOT NULL CHECK (status IN ('pending', 'active', 'failed', 'used', 'superseded'))
);
CREATE INDEX IF NOT EXISTS email_verifications_lookup ON email_verifications(email, purpose, created_at);
CREATE INDEX IF NOT EXISTS email_verifications_expiry ON email_verifications(expires_at);
CREATE UNIQUE INDEX IF NOT EXISTS email_verifications_current
    ON email_verifications(email, purpose) WHERE status IN ('pending', 'active');

CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at INTEGER NOT NULL
);

-- 调用统计与并发租约，不是聊天历史；保留最近 30 天元数据。
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

-- 历史消息与模型调用统计分开保存；只新增表，不重建账号。
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title TEXT NOT NULL CHECK (length(title) BETWEEN 1 AND 80),
    title_custom INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    last_sequence INTEGER NOT NULL DEFAULT 0 CHECK (last_sequence >= 0)
);
CREATE INDEX IF NOT EXISTS conversations_owner_updated ON conversations(user_id, updated_at DESC, id DESC);

CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    client_message_id TEXT NOT NULL,
    sequence INTEGER NOT NULL CHECK (sequence > 0),
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL CHECK (length(content) BETWEEN 1 AND 20000),
    status TEXT NOT NULL CHECK (status IN ('pending', 'completed', 'failed')),
    model TEXT,
    request_id TEXT NOT NULL,
    reply_to_id TEXT UNIQUE REFERENCES messages(id) ON DELETE CASCADE,
    lease_expires_at INTEGER NOT NULL DEFAULT 0,
    error_code TEXT,
    error_message TEXT,
    context_trimmed INTEGER NOT NULL DEFAULT 0,
    answer_truncated INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL,
    UNIQUE (conversation_id, sequence),
    UNIQUE (conversation_id, client_message_id, role),
    CHECK ((role = 'user' AND reply_to_id IS NULL) OR (role = 'assistant' AND reply_to_id IS NOT NULL AND status = 'completed'))
);
CREATE UNIQUE INDEX IF NOT EXISTS messages_one_pending ON messages(conversation_id) WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS messages_pending_expiry ON messages(status, lease_expires_at);
