from concurrent.futures import ThreadPoolExecutor
import asyncio
import sqlite3
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.main import get_llm_provider
from app.conversations.schemas import SendMessage
from app.conversations.store import HistoryError
from app.migrations import initialize_database, SQL_DIR
from app.integrations.llm import LLMError


class Model:
    def __init__(self):
        self.calls = []
        self.fail = False

    async def chat(self, messages):
        self.calls.append(messages)
        if self.fail:
            raise LLMError("模型暂时不可用", "LLM_UNAVAILABLE", 503)
        return "回答：" + messages[-1].content


@pytest.fixture
def model(app):
    instance = Model()
    app.dependency_overrides[get_llm_provider] = lambda: instance
    return instance


def create(client):
    result = client.post("/api/conversations", json={"id": uuid4().hex})
    assert result.status_code == 201, result.text
    return result.json()["id"]


def question(content="你好", sequence=0, **extra):
    return {"content": content, "client_message_id": uuid4().hex, "expected_last_sequence": sequence, **extra}


def test_persistence_authoritative_context_replay_and_conflict(logged_in, model):
    identifier = create(logged_in)
    endpoint = f"/api/conversations/{identifier}/messages"
    first = question("我叫小明")
    result = logged_in.post(endpoint, json=first)
    assert result.status_code == 200, result.text
    assert result.json()["conversation"]["title"] == "我叫小明"
    replay = logged_in.post(endpoint, json=first)
    assert replay.json()["replayed"] is True
    assert len(model.calls) == 1
    assert replay.json()["assistant_message"]["id"] == result.json()["assistant_message"]["id"]
    assert logged_in.post(endpoint, json={**first, "content": "篡改编号"}).status_code == 409
    assert logged_in.post(endpoint, json=question("过期页面", 0)).status_code == 409
    assert logged_in.post(endpoint, json={**question("伪造历史", 2), "messages": []}).status_code == 422
    followup = logged_in.post(endpoint, json=question("我叫什么？", 2))
    assert followup.status_code == 200, followup.text
    assert [(m.role, m.content) for m in model.calls[-1]] == [("user", "我叫小明"), ("assistant", "回答：我叫小明"), ("user", "我叫什么？")]
    saved = logged_in.get(endpoint).json()
    assert [m["sequence"] for m in saved["messages"]] == [1, 2, 3, 4]
    assert all(m["status"] == "completed" for m in saved["messages"])
    assert "user_id" not in saved["conversation"]


def test_failure_retry_keeps_single_question_and_new_question_skips_failures(logged_in, model):
    identifier = create(logged_in)
    endpoint = f"/api/conversations/{identifier}/messages"
    data = question("失败问题")
    model.fail = True
    assert logged_in.post(endpoint, json=data).status_code == 503
    saved = logged_in.get(endpoint).json()["messages"]
    assert len(saved) == 1 and saved[0]["status"] == "failed"
    assert logged_in.post(endpoint, json=data).status_code == 409
    model.fail = False
    response = logged_in.post(endpoint, json={**data, "retry": True})
    assert response.status_code == 200
    assert response.json()["user_message"]["id"] == saved[0]["id"]
    model.fail = True
    assert logged_in.post(endpoint, json=question("另一个失败问题", 2)).status_code == 503
    model.fail = False
    assert logged_in.post(endpoint, json=question("修改后的问题", 3)).status_code == 200
    assert "另一个失败问题" not in [m.content for m in model.calls[-1]]
    assert len(logged_in.get(endpoint).json()["messages"]) == 5


def test_owner_isolation_origin_guard_and_delete_cascade(logged_in, app, model):
    identifier = create(logged_in)
    endpoint = f"/api/conversations/{identifier}"
    assert logged_in.post(endpoint + "/messages", json=question()).status_code == 200
    other = app.state.auth_store.create_user("another_user", "unused-test-hash")
    token = app.state.auth_store.create_session(other.id, 3600, None)
    with TestClient(app) as stranger:
        stranger.cookies.set("zhiyan_session", token)
        assert stranger.get("/api/conversations").json()["items"] == []
        assert stranger.get(endpoint + "/messages").status_code == 404
        assert stranger.post(endpoint + "/messages", json=question()).status_code == 404
        assert stranger.patch(endpoint, json={"title": "窃取"}).status_code == 404
        assert stranger.delete(endpoint).status_code == 404
    assert logged_in.patch(endpoint, json={"title": "新的标题"}, headers={"Origin": "https://evil.invalid"}).status_code == 403
    assert logged_in.delete(endpoint, headers={"Origin": "https://evil.invalid"}).status_code == 403
    renamed = logged_in.patch(endpoint, json={"title": "  我的对话  "})
    assert renamed.json()["title"] == "我的对话"
    assert logged_in.delete(endpoint).status_code == 204
    assert logged_in.get(endpoint + "/messages").status_code == 404
    with app.state.auth_store.connect() as db:
        assert db.execute("SELECT count(*) FROM messages WHERE conversation_id = ?", (identifier,)).fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM users").fetchone()[0] == 2


def test_pagination_and_create_idempotency(logged_in, model):
    ids = [create(logged_in) for _ in range(3)]
    assert logged_in.post("/api/conversations", json={"id": ids[0]}).json()["id"] == ids[0]
    first = logged_in.get("/api/conversations?limit=2").json()
    second = logged_in.get("/api/conversations", params={"limit": 2, "cursor": first["next_cursor"]}).json()
    assert {row["id"] for row in first["items"] + second["items"]} == set(ids)
    assert second["next_cursor"] is None
    endpoint = f"/api/conversations/{ids[0]}/messages"
    for index in range(3):
        assert logged_in.post(endpoint, json=question(str(index), index * 2)).status_code == 200
    latest = logged_in.get(endpoint, params={"limit": 3}).json()
    older = logged_in.get(endpoint, params={"limit": 3, "before": latest["next_before"]}).json()
    assert [row["sequence"] for row in older["messages"] + latest["messages"]] == list(range(1, 7))
    assert older["next_before"] is None


def test_atomic_pending_lease_and_late_result_cannot_replace_retry(logged_in, app):
    identifier = create(logged_in)
    user_id = logged_in.get("/api/auth/me").json()["user"]["id"]
    store = app.state.conversations
    data = SendMessage(**question())
    def begin(number):
        try:
            return store.begin(user_id, identifier, data, f"attempt-{number}", 90)
        except HistoryError as exc:
            return exc.code
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(begin, [1, 2]))
    assert sum(isinstance(result, dict) for result in results) == 1
    assert "TURN_PENDING" in results
    assert logged_in.delete(f"/api/conversations/{identifier}").status_code == 409
    with app.state.auth_store.connect() as db:
        db.execute("UPDATE messages SET lease_expires_at = 0 WHERE conversation_id = ?", (identifier,))
    assert store.read(user_id, identifier)["messages"][0]["status"] == "failed"
    retried = store.begin(user_id, identifier, data.model_copy(update={"retry": True}), "new-attempt", 90)
    from app.schemas import ChatResponse
    with pytest.raises(HistoryError):
        store.complete(user_id, identifier, retried["message_id"], "attempt-1", ChatResponse(content="旧回答", model="test"))
    final = store.complete(user_id, identifier, retried["message_id"], "new-attempt", ChatResponse(content="新回答", model="test"))
    assert final["assistant_message"]["content"] == "新回答"


def test_v2_migration_preserves_accounts_and_is_repeatable(tmp_path):
    path = tmp_path / "upgrade.sqlite3"
    # 从旧的六张表结构升级，避免只覆盖全新建库路径。
    old_schema = (SQL_DIR / "schema.sql").read_text(encoding="utf-8").split("-- 历史消息与模型调用统计分开保存")[0]
    with sqlite3.connect(path) as db:
        db.executescript(old_schema)
        db.execute("INSERT INTO users (username, password_hash, created_at) VALUES ('preserved', 'hash', 1)")
        db.executemany("INSERT INTO schema_migrations VALUES (?, 1)", [(1,), (2,)])
    initialize_database(path)
    initialize_database(path)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT username FROM users").fetchone()[0] == "preserved"
        assert db.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall() == [(1,), (2,), (3,), (4,), (5,), (6,), (7,), (8,), (9,), (10,), (11,)]
        assert {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")} >= {"messages", "conversations"}


def test_logout_login_keeps_history_and_anonymous_cannot_read(logged_in, app, model):
    identifier = create(logged_in)
    assert logged_in.post(f"/api/conversations/{identifier}/messages", json=question()).status_code == 200
    assert logged_in.post("/api/auth/logout").status_code == 204
    assert logged_in.get(f"/api/conversations/{identifier}/messages").status_code == 401
    assert logged_in.post("/api/auth/login", json={"username": "test_user", "password": "Test-password-2026!"}).status_code == 200
    assert len(logged_in.get(f"/api/conversations/{identifier}/messages").json()["messages"]) == 2


def test_full_history_outlives_model_context_window(logged_in, app):
    from app.schemas import ChatResponse
    identifier = create(logged_in)
    user_id = logged_in.get("/api/auth/me").json()["user"]["id"]
    store = app.state.conversations
    for index in range(27):
        request_id = f"turn-{index}"
        state = store.begin(user_id, identifier, SendMessage(**question(f"问题{index}", index * 2)), request_id, 90)
        store.complete(user_id, identifier, state["message_id"], request_id, ChatResponse(content=f"回答{index}", model="test"))
    next_turn = store.begin(user_id, identifier, SendMessage(**question("继续", 54)), "turn-27", 90)
    assert next_turn["trimmed"] is True
    assert len(next_turn["messages"]) == 51
    assert next_turn["messages"][0].content == "问题2"
    assert next_turn["messages"][-1].content == "继续"
    assert len(store.read(user_id, identifier, limit=100)["messages"]) == 55


def test_cancelled_generation_releases_pending_turn(logged_in, app):
    identifier = create(logged_in)
    user_id = logged_in.get("/api/auth/me").json()["user"]["id"]
    async def scenario():
        started = asyncio.Event()
        class WaitingModel:
            async def chat(self, messages):
                started.set()
                await asyncio.Event().wait()
        task = asyncio.create_task(app.state.conversation_service.send(user_id, identifier, SendMessage(**question()), "cancel-test", WaitingModel()))
        await asyncio.wait_for(started.wait(), timeout=5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    asyncio.run(scenario())
    row = app.state.conversations.read(user_id, identifier)["messages"][0]
    assert row["status"] == "failed"
    assert row["error_code"] == "REQUEST_CANCELLED"
    with app.state.auth_store.connect() as db:
        assert db.execute("SELECT state FROM model_calls WHERE request_id = 'cancel-test'").fetchone()[0] == "cancelled"
