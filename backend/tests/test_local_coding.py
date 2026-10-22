"""User-run offline protocol regression tests. No model or local filesystem tools run."""

import asyncio
import json
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.coding.local_schemas import LocalCreate, LocalResults, LocalConversationCreate
from app.coding.local_service import LOCAL_PROMPT, LOCAL_TOOLS
from app.coding.local_store import public_local
from app.coding.model import CodingReply
from app.coding.schemas import CodingError


PATH_POLICY = json.loads((Path(__file__).parent / "fixtures" / "local_path_policy.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("path", PATH_POLICY["blocked"])
def test_local_create_rejects_sensitive_and_unsafe_paths(path):
    with pytest.raises(ValidationError):
        LocalCreate(instruction="test", directory_name="demo", conversation_id=uuid4(),
                    client_task_id=uuid4(), grant_token="x" * 64, paths=[path])


@pytest.mark.parametrize("path", PATH_POLICY["allowed"])
def test_local_create_preserves_supported_source_paths(path):
    data = LocalCreate(instruction="test", directory_name="demo", conversation_id=uuid4(),
                       client_task_id=uuid4(), grant_token="x" * 64, paths=[path])
    assert data.paths == [path]


def create_task(logged_in, app):
    owner = logged_in.get("/api/auth/me").json()["user"]["id"]
    store = app.state.local_coding.store
    conversation = store.create_conversation(owner, LocalConversationCreate(client_conversation_id=uuid4(), directory_name="demo"))
    data = LocalCreate(instruction="Read main.py", directory_name="demo", client_task_id=uuid4(),
                       conversation_id=conversation["id"], grant_token=uuid4().hex + uuid4().hex, paths=["main.py"])
    task = store.create(owner, data, app.state.settings, LOCAL_PROMPT)
    return owner, data, task, store


def tool_reply():
    return CodingReply("先读取代码。", [{"id": "call-1", "type": "function", "function": {
        "name": "read_file", "arguments": json.dumps({"path": "main.py"}),
    }}])


def result(data, **changes):
    return LocalResults(**({"grant_token": data.grant_token, "round": 1,
                           "results": [{"tool_call_id": "call-1", "result": '{"ok":true,"content":"pass"}'}]} | changes))


def test_owner_grant_idempotency_and_completion(logged_in, app):
    owner, data, task, store = create_task(logged_in, app)
    identifier = task["id"]
    assert store.create(owner, data, app.state.settings, LOCAL_PROMPT)["id"] == identifier
    with pytest.raises(CodingError):
        store.create(owner, data.model_copy(update={"instruction": "different"}), app.state.settings, LOCAL_PROMPT)
    with pytest.raises(CodingError):
        store.get(owner + 1000, identifier)
    assert "grant_hash" not in public_local(task)
    assert data.grant_token not in json.dumps(public_local(task))
    assert store.claim(owner, identifier, 12)["round"] == 1
    assert store.claim(owner, identifier, 12) is None
    store.reply(owner, identifier, tool_reply())
    with pytest.raises(CodingError) as invalid:
        store.results(owner, identifier, result(data, grant_token="x" * 64))
    assert invalid.value.status == 403
    with pytest.raises(CodingError):
        store.results(owner, identifier, result(data, round=2))
    with pytest.raises(CodingError):
        store.results(owner, identifier, result(data, results=[{"tool_call_id": "wrong", "result": '{"ok":true}'}]))
    acknowledged = store.results(owner, identifier, result(data))
    assert acknowledged["status"] == "queued"
    replay = store.results(owner, identifier, result(data))
    assert len(replay["messages"]) == len(acknowledged["messages"])
    assert store.claim(owner, identifier, 12)["round"] == 2
    store.reply(owner, identifier, CodingReply("已阅读。", []))
    final = store.get(owner, identifier)
    assert final["status"] == "completed"
    assert final["messages"] == final["pending_tools"] == []
    assert final["summary"] == "已阅读。"


def test_cancel_rejects_late_browser_results(logged_in, app):
    owner, data, task, store = create_task(logged_in, app)
    identifier = task["id"]
    store.claim(owner, identifier, 12)
    store.reply(owner, identifier, tool_reply())
    store.stop(owner, identifier)
    with pytest.raises(CodingError):
        store.results(owner, identifier, result(data))
    store.reply(owner, identifier, CodingReply("late success", []))
    assert store.get(owner, identifier)["status"] == "cancelled"
    assert store.get(owner, identifier)["messages"] == []


def test_deadline_and_restart_do_not_resume_tools(logged_in, app):
    owner, data, task, store = create_task(logged_in, app)
    with app.state.auth_store.connect() as db:
        db.execute("UPDATE local_coding_tasks SET deadline_at=0 WHERE id=?", (task["id"],))
    assert store.get(owner, task["id"])["status"] == "interrupted"
    assert store.claim(owner, task["id"], 12) is None
    another = store.create(owner, data.model_copy(update={"client_task_id": uuid4()}), app.state.settings, LOCAL_PROMPT)
    store.claim(owner, another["id"], 12)
    store.reply(owner, another["id"], tool_reply())
    store.recover_interrupted()
    recovered = store.get(owner, another["id"])
    assert recovered["status"] == "interrupted"
    assert recovered["messages"] == recovered["pending_tools"] == []


def test_browser_tools_only_and_validation():
    assert {tool["function"]["name"] for tool in LOCAL_TOOLS} == {
        "list_files", "read_file", "search_code", "write_file", "replace_text",
    }
    with pytest.raises(ValidationError):
        LocalCreate(instruction="test", directory_name="demo", conversation_id=uuid4(), client_task_id=uuid4(), grant_token="x" * 64, paths=["../secret.py"])
    with pytest.raises(ValidationError):
        LocalResults(grant_token="x" * 64, round=1, results=[{"tool_call_id": "a", "result": '[]'}])


def test_capabilities_require_login(client):
    assert client.get("/api/coding/local/capabilities").status_code == 401


def test_local_capabilities_independent_of_retired_zip(logged_in, app):
    response = logged_in.get("/api/coding/local/capabilities")
    assert response.status_code == 200
    assert response.json() == {
        "model": app.state.settings.qwen_coding_model or app.state.settings.qwen_model,
        "max_steps": app.state.settings.coding_max_steps,
    }
    assert logged_in.get("/api/coding/projects").status_code == 404
    assert logged_in.post("/api/coding/projects").status_code == 404
    assert logged_in.get("/api/coding/capabilities").status_code == 404
    assert not hasattr(app.state, "coding_service")


def test_two_model_rounds_wait_for_browser_and_meter_usage(logged_in, app):
    owner, data, task, store = create_task(logged_in, app)
    service = app.state.local_coding

    class FakeModel:
        name = "offline-local-model"
        calls = 0

        async def call(self, messages):
            self.calls += 1
            if self.calls == 1:
                return tool_reply()
            assert messages[-1]["role"] == "tool"
            return CodingReply("浏览器已返回文件内容。", [])

    service.model = FakeModel()
    asyncio.run(service._run(owner, task["id"]))
    assert store.get(owner, task["id"])["status"] == "waiting_tools"
    store.results(owner, task["id"], result(data))
    asyncio.run(service._run(owner, task["id"]))
    assert store.get(owner, task["id"])["status"] == "completed"
    assert app.state.model_calls.usage(owner, app.state.settings)["requests"] == 2
