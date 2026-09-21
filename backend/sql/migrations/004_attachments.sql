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
