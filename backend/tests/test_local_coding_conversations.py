"""User-run regression source: no real model calls or browser filesystem access."""

import json
import sqlite3
from uuid import UUID, uuid4

import pytest

from app.coding.local_schemas import LocalConversationCreate
from app.coding.local_service import LOCAL_PROMPT
from app.coding.local_store import HISTORY_MAX_CHARS, historical_messages
from app.coding.model import CodingReply
from app.coding.schemas import CodingError
from app.coding.store import encode
from app.migrations import SQL_DIR, initialize_database
from tests.test_local_coding import create_task


def test_followup_uses_only_its_owned_conversation_memory(logged_in, app):
    owner, data, first, store = create_task(logged_in, app)
    store.claim(owner, first["id"], 12)
    store.reply(owner, first["id"], CodingReply("已创建登录页面，按钮为蓝色。", []))
    another = store.create_conversation(owner, LocalConversationCreate(
        client_conversation_id=uuid4(), directory_name="other-project"))
    unrelated = store.create(owner, data.model_copy(update={
        "client_task_id": uuid4(), "conversation_id": UUID(another["id"]),
        "directory_name": "other-project", "instruction": "unrelated-project-secret",
    }), app.state.settings, LOCAL_PROMPT)
    store.stop(owner, unrelated["id"])
    followup = store.create(owner, data.model_copy(update={
        "client_task_id": uuid4(), "instruction": "把刚才的按钮改成黑色", "paths": ["new.html"],
    }), app.state.settings, LOCAL_PROMPT)
    assert followup["turn_index"] == 2
    context = encode(followup["messages"])
    assert "已创建登录页面" in context and "unrelated-project-secret" not in context
    assert "new.html" in followup["messages"][-1]["content"]
    assert all(message["role"] in {"system", "user", "assistant"} for message in followup["messages"])
    detail = store.get_conversation(owner, first["conversation_id"])
    assert [task["id"] for task in detail["tasks"]] == [first["id"], followup["id"]]
    assert detail["turn_count"] == 2
    assert store.get(owner, first["id"])["messages"] == []


def test_conversations_are_owned_and_directory_bound(logged_in, app):
    owner, data, first, store = create_task(logged_in, app)
    for action in (store.get_conversation, store.remove_conversation):
        with pytest.raises(CodingError) as denied:
            action(owner + 1000, first["conversation_id"])
        assert denied.value.status == 404
    with pytest.raises(CodingError):
        store.create(owner + 1000, data, app.state.settings, LOCAL_PROMPT)
    store.stop(owner, first["id"])
    with pytest.raises(CodingError) as wrong_directory:
        store.create(owner, data.model_copy(update={"client_task_id": uuid4(), "directory_name": "elsewhere"}),
                     app.state.settings, LOCAL_PROMPT)
    assert wrong_directory.value.status == 409
    request = LocalConversationCreate(client_conversation_id=uuid4(), directory_name="demo")
    one = store.create_conversation(owner, request)
    assert store.create_conversation(owner, request)["id"] == one["id"]
    with pytest.raises(CodingError):
        store.create_conversation(owner, request.model_copy(update={"directory_name": "changed"}))


def test_cancel_keeps_conversation_and_monotonic_turn_numbers(logged_in, app):
    owner, data, first, store = create_task(logged_in, app)
    with pytest.raises(CodingError):
        store.remove_conversation(owner, first["conversation_id"])
    store.stop(owner, first["id"])
    second = store.create(owner, data.model_copy(update={"client_task_id": uuid4()}), app.state.settings, LOCAL_PROMPT)
    assert second["turn_index"] == 2
    store.stop(owner, second["id"])
    store.remove(owner, second["id"])
    third = store.create(owner, data.model_copy(update={"client_task_id": uuid4()}), app.state.settings, LOCAL_PROMPT)
    assert third["turn_index"] == 3
    store.stop(owner, third["id"])
    assert store.get_conversation(owner, first["conversation_id"])["turn_count"] == 2
    store.remove_conversation(owner, first["conversation_id"])
    with pytest.raises(CodingError):
        store.get(owner, first["id"])


def test_history_budget_preserves_order_and_never_reuses_tool_messages():
    rows = [{"instruction": f"turn-{index} " + "\\\"" * 3000,
             "summary": "历史回答" * 4000, "error": "", "status": "completed"}
            for index in range(12, 0, -1)]
    messages = historical_messages(rows)
    assert messages
    # Pair serialization accounts for all content; list punctuation adds a small fixed overhead.
    assert sum(len(encode(messages[index:index + 2])) for index in range(0, len(messages), 2)) <= HISTORY_MAX_CHARS
    numbers = [int(message["content"].split()[0].split("-")[1]) for message in messages if message["role"] == "user"]
    assert numbers == sorted(numbers)
    assert numbers[-1] == 12
    assert not any("tool_calls" in message or "tool_call_id" in message for message in messages)


def test_conversation_http_history_does_not_replay_tools(logged_in, app):
    owner, _, first, store = create_task(logged_in, app)
    identifier = first["conversation_id"]
    response = logged_in.get(f"/api/coding/local/conversations/{identifier}")
    assert response.status_code == 200
    assert response.json()["tasks"][0]["pending_tools"] == []
    assert logged_in.delete(f"/api/coding/local/conversations/{identifier}").status_code == 409
    store.stop(owner, first["id"])
    assert logged_in.delete(f"/api/coding/local/conversations/{identifier}").status_code == 204
    assert logged_in.get(f"/api/coding/local/conversations/{identifier}").status_code == 404


def test_v7_migration_preserves_legacy_tasks_and_is_repeatable(tmp_path):
    path = tmp_path / "coding-upgrade.sqlite3"
    prefix = (SQL_DIR / "schema.sql").read_text(encoding="utf-8").split("-- Browser-local tasks")[0]
    with sqlite3.connect(path) as db:
        db.executescript(prefix)
        db.executescript((SQL_DIR / "migrations/007_local_coding.sql").read_text(encoding="utf-8"))
        db.execute("INSERT INTO users (username,password_hash,created_at) VALUES ('kept-user','hash',1)")
        owner = db.execute("SELECT id FROM users").fetchone()[0]
        db.executemany("INSERT INTO schema_migrations VALUES (?,1)", [(version,) for version in range(1, 8)])
        identifier = uuid4().hex
        db.execute("""INSERT INTO local_coding_tasks
            (id,user_id,client_task_id,grant_hash,request_hash,directory_name,instruction,status,
             summary,steps_json,created_at,updated_at,deadline_at)
            VALUES (?,?,?,?,?,?,?,'completed',?,?,?,?,?)""",
            (identifier, owner, uuid4().hex, "grant-hash", "request-hash", "project", "保留旧需求",
             "保留旧回答", json.dumps([{"message": "kept step"}]), 1, 2, 3))
    initialize_database(path)
    initialize_database(path)
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA foreign_keys=ON")
        row = db.execute("SELECT id,conversation_id,turn_index,summary,steps_json,grant_hash FROM local_coding_tasks").fetchone()
        assert row[:4] == (identifier, identifier, 1, "保留旧回答")
        assert json.loads(row[4])[0]["message"] == "kept step" and row[5] == "grant-hash"
        assert db.execute("SELECT turn_count,last_turn_index FROM local_coding_conversations").fetchone() == (1, 1)
        assert db.execute("SELECT max(version) FROM schema_migrations").fetchone()[0] == 11
        assert db.execute("SELECT project_id FROM local_coding_conversations").fetchone()[0] == identifier
        assert db.execute("SELECT id FROM local_coding_projects").fetchone()[0] == identifier
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        db.execute("DELETE FROM local_coding_conversations WHERE id=?", (identifier,))
        assert db.execute("SELECT count(*) FROM local_coding_tasks").fetchone()[0] == 0
