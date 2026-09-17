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
