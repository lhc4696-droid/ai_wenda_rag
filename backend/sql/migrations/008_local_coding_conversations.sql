-- Executed in the existing migration transaction. Each legacy task becomes its own conversation.
-- Rebuild only protocol rows to enforce NOT NULL/FK constraints; no user project files are touched.
CREATE TABLE local_coding_conversations (
    id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    client_conversation_id TEXT NOT NULL,
    directory_name TEXT NOT NULL,
    title TEXT NOT NULL,
    last_turn_index INTEGER NOT NULL DEFAULT 0 CHECK(last_turn_index >= 0),
    turn_count INTEGER NOT NULL DEFAULT 0 CHECK(turn_count >= 0),
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    UNIQUE(user_id,client_conversation_id)
);
CREATE INDEX local_coding_conversations_owner ON local_coding_conversations(user_id,updated_at DESC);
INSERT INTO local_coding_conversations
    (id,user_id,client_conversation_id,directory_name,title,last_turn_index,turn_count,created_at,updated_at)
    SELECT id,user_id,id,directory_name,substr(instruction,1,80),1,1,created_at,updated_at
    FROM local_coding_tasks;

ALTER TABLE local_coding_tasks RENAME TO local_coding_tasks_v7;
CREATE TABLE local_coding_tasks (
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
INSERT INTO local_coding_tasks
    (id,user_id,conversation_id,turn_index,client_task_id,grant_hash,request_hash,directory_name,
     instruction,status,round,messages_json,pending_tools_json,steps_json,last_result_round,
     last_result_hash,summary,error,created_at,updated_at,deadline_at)
    SELECT id,user_id,id,1,client_task_id,grant_hash,request_hash,directory_name,
           instruction,status,round,messages_json,pending_tools_json,steps_json,last_result_round,
           last_result_hash,summary,error,created_at,updated_at,deadline_at
    FROM local_coding_tasks_v7;
DROP TABLE local_coding_tasks_v7;
CREATE INDEX local_coding_owner ON local_coding_tasks(user_id,created_at DESC);
CREATE UNIQUE INDEX local_coding_active ON local_coding_tasks(user_id) WHERE status IN ('queued','running','waiting_tools');
