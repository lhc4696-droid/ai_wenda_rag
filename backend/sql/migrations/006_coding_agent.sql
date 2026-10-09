-- Add isolated coding projects and immutable per-task source snapshots.
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
