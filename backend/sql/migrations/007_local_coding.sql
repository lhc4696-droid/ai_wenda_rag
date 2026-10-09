CREATE TABLE IF NOT EXISTS local_coding_tasks (
    id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
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
    UNIQUE(user_id,client_task_id)
);
CREATE INDEX IF NOT EXISTS local_coding_owner ON local_coding_tasks(user_id,created_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS local_coding_active ON local_coding_tasks(user_id) WHERE status IN ('queued','running','waiting_tools');
