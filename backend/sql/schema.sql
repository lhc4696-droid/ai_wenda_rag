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

-- 每个账号一份兼容接口配置；密钥只存 Fernet 密文，与所属账号和服务地址绑定。
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
    source_mode TEXT NOT NULL DEFAULT 'legacy' CHECK (source_mode IN ('legacy', 'chat', 'documents')),
    sources_json TEXT NOT NULL DEFAULT '[]',
    created_at INTEGER NOT NULL,
    UNIQUE (conversation_id, sequence),
    UNIQUE (conversation_id, client_message_id, role),
    CHECK ((role = 'user' AND reply_to_id IS NULL) OR (role = 'assistant' AND reply_to_id IS NOT NULL AND status = 'completed'))
);
CREATE UNIQUE INDEX IF NOT EXISTS messages_one_pending ON messages(conversation_id) WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS messages_pending_expiry ON messages(status, lease_expires_at);

-- 私有附件与消息关联。删除会话先标记文件待清理，再由清理任务删除磁盘内容。
CREATE TABLE IF NOT EXISTS attachments (
    id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    conversation_id TEXT REFERENCES conversations(id) ON DELETE SET NULL,
    filename TEXT NOT NULL,
    extension TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('image', 'document')),
    media_type TEXT NOT NULL DEFAULT 'application/octet-stream',
    size INTEGER NOT NULL DEFAULT 0,
    reserved_bytes INTEGER NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('uploading', 'ready', 'failed', 'deleting')),
    extracted_text TEXT NOT NULL DEFAULT '',
    warning TEXT NOT NULL DEFAULT '',
    error_message TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS attachments_owner ON attachments(user_id, status);
CREATE INDEX IF NOT EXISTS attachments_expiry ON attachments(status, expires_at);
CREATE TABLE IF NOT EXISTS message_attachments (
    message_id TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    attachment_id TEXT NOT NULL REFERENCES attachments(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    PRIMARY KEY(message_id, attachment_id),
    UNIQUE(message_id, position)
);
CREATE INDEX IF NOT EXISTS message_attachments_file ON message_attachments(attachment_id);
CREATE TRIGGER IF NOT EXISTS conversations_cleanup_attachments
BEFORE DELETE ON conversations
BEGIN
    UPDATE attachments SET status = 'deleting' WHERE conversation_id = OLD.id;
END;

-- Coding projects retain imported source; each task works on its own snapshot.
CREATE TABLE IF NOT EXISTS coding_projects (
    id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL CHECK(length(name) BETWEEN 1 AND 80),
    source_files_json TEXT NOT NULL,
    warnings_json TEXT NOT NULL DEFAULT '[]',
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS coding_projects_owner ON coding_projects(user_id,updated_at DESC);
CREATE TABLE IF NOT EXISTS coding_tasks (
    id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    project_id TEXT NOT NULL REFERENCES coding_projects(id) ON DELETE CASCADE,
    client_task_id TEXT NOT NULL,
    base_task_id TEXT REFERENCES coding_tasks(id) ON DELETE SET NULL,
    instruction TEXT NOT NULL CHECK(length(instruction) BETWEEN 1 AND 6000),
    request_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('queued','running','completed','failed','cancelled','interrupted')),
    baseline_files_json TEXT NOT NULL,
    files_json TEXT NOT NULL,
    steps_json TEXT NOT NULL DEFAULT '[]',
    checks_json TEXT NOT NULL DEFAULT '[]',
    summary TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '',
    cancel_requested INTEGER NOT NULL DEFAULT 0 CHECK(cancel_requested IN (0,1)),
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    UNIQUE(user_id,client_task_id)
);
CREATE INDEX IF NOT EXISTS coding_tasks_project ON coding_tasks(user_id,project_id,created_at DESC);
CREATE INDEX IF NOT EXISTS coding_tasks_active ON coding_tasks(status,user_id);
CREATE UNIQUE INDEX IF NOT EXISTS coding_tasks_one_active_user ON coding_tasks(user_id) WHERE status IN ('queued','running');

-- Browser-local tasks store orchestration state, not a server-mounted client directory.
CREATE TABLE IF NOT EXISTS local_coding_projects (
    id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    client_project_id TEXT NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    name_request TEXT NOT NULL DEFAULT '',
    directory_name TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    UNIQUE(user_id,client_project_id)
);
CREATE INDEX IF NOT EXISTS local_coding_projects_owner ON local_coding_projects(user_id,updated_at DESC);
CREATE TABLE IF NOT EXISTS local_coding_project_numbers (
    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    last_number INTEGER NOT NULL DEFAULT 0 CHECK(last_number >= 0)
);
CREATE TABLE IF NOT EXISTS local_coding_conversations (
    id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    project_id TEXT NOT NULL REFERENCES local_coding_projects(id) ON DELETE CASCADE,
    client_conversation_id TEXT NOT NULL,
    directory_name TEXT NOT NULL,
    title TEXT NOT NULL,
    last_turn_index INTEGER NOT NULL DEFAULT 0 CHECK(last_turn_index >= 0),
    turn_count INTEGER NOT NULL DEFAULT 0 CHECK(turn_count >= 0),
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    UNIQUE(user_id,client_conversation_id)
);
CREATE INDEX IF NOT EXISTS local_coding_conversations_owner ON local_coding_conversations(user_id,updated_at DESC);
CREATE INDEX IF NOT EXISTS local_coding_conversations_project ON local_coding_conversations(user_id,project_id,updated_at DESC);
-- Also enforce non-null/owner/name invariants on upgraded databases (ALTER adds a nullable column).
CREATE TRIGGER IF NOT EXISTS local_coding_conversation_project_insert
BEFORE INSERT ON local_coding_conversations
WHEN NOT EXISTS (SELECT 1 FROM local_coding_projects p WHERE p.id=NEW.project_id AND p.user_id=NEW.user_id AND p.directory_name=NEW.directory_name)
BEGIN
    SELECT RAISE(ABORT, 'invalid local coding project');
END;
CREATE TRIGGER IF NOT EXISTS local_coding_conversation_project_update
BEFORE UPDATE OF project_id,user_id,directory_name ON local_coding_conversations
WHEN NEW.project_id IS NOT OLD.project_id OR NOT EXISTS (SELECT 1 FROM local_coding_projects p WHERE p.id=NEW.project_id AND p.user_id=NEW.user_id AND p.directory_name=NEW.directory_name)
BEGIN
    SELECT RAISE(ABORT, 'invalid local coding project');
END;
CREATE TABLE IF NOT EXISTS local_coding_tasks (
    id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    conversation_id TEXT NOT NULL REFERENCES local_coding_conversations(id) ON DELETE CASCADE,
    turn_index INTEGER NOT NULL CHECK(turn_index >= 1),
    client_task_id TEXT NOT NULL,
    grant_hash TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    directory_name TEXT NOT NULL,
    instruction TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('queued','running','waiting_tools','completed','failed','cancelled','interrupted')),
    round INTEGER NOT NULL DEFAULT 0,
    messages_json TEXT NOT NULL DEFAULT '[]',
    pending_tools_json TEXT NOT NULL DEFAULT '[]',
    steps_json TEXT NOT NULL DEFAULT '[]',
    last_result_round INTEGER NOT NULL DEFAULT 0,
    last_result_hash TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    deadline_at INTEGER NOT NULL,
    UNIQUE(user_id,client_task_id),
    UNIQUE(conversation_id,turn_index)
);
CREATE INDEX IF NOT EXISTS local_coding_owner ON local_coding_tasks(user_id,created_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS local_coding_active ON local_coding_tasks(user_id) WHERE status IN ('queued','running','waiting_tools');
