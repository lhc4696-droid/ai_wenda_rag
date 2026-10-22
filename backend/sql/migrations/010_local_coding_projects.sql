-- Each legacy conversation becomes a distinct project, even when names match.
-- Reuse its ID so the browser's existing directory capability remains valid.
CREATE TABLE local_coding_projects (
    id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    client_project_id TEXT NOT NULL,
    directory_name TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    UNIQUE(user_id,client_project_id)
);
INSERT INTO local_coding_projects (id,user_id,client_project_id,directory_name,created_at,updated_at)
    SELECT id,user_id,id,directory_name,created_at,updated_at FROM local_coding_conversations;
ALTER TABLE local_coding_conversations ADD COLUMN project_id TEXT REFERENCES local_coding_projects(id) ON DELETE CASCADE;
UPDATE local_coding_conversations SET project_id=id;
