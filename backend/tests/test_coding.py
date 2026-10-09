"""Offline tests for retained coding primitives, not the retired ZIP HTTP product.

These tests are supplied for user-run verification and never launch Docker.
"""

import asyncio
from io import BytesIO
import json
from uuid import uuid4
from zipfile import ZipFile, ZIP_DEFLATED

import pytest

from app.coding.model import CodingReply
from app.coding.schemas import CodingError, CreateTask, public_task
from app.coding.service import CodingService
from app.coding.store import CodingStore, retire_zip_tasks
from app.coding.tools import CodingTools
from app.coding.workspace import parse_project_zip, validate_files, export_project_zip


def source_zip(files):
    stream = BytesIO()
    with ZipFile(stream, "w", ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return stream.getvalue()


class FakeSandbox:
    async def run(self, files, kind, task_id):
        return {"kind": kind, "status": "passed", "exit_code": 0, "output": "Fake check; no code executed."}


def test_zip_filters_secrets_and_rejects_unsafe_paths():
    imported = parse_project_zip(source_zip({
        "example/main.py": "print('hi')\n", "example/.env": "SECRET=test",
        "example/venv/lib.py": "ignored = True", "example/image.png": b"image",
    }), "example.zip")
    assert imported["files"] == {"main.py": "print('hi')\n"}
    assert imported["warnings"]
    with pytest.raises(CodingError):
        parse_project_zip(source_zip({"../main.py": "pass"}), "bad.zip")
    with pytest.raises(CodingError):
        validate_files({"main.py": "pass", "MAIN.py": "pass"})
    with pytest.raises(CodingError):
        validate_files({"../main.py": "pass"})
    with pytest.raises(CodingError):
        validate_files({"\ud800.py": "pass"})
    with ZipFile(BytesIO(export_project_zip(imported["files"]))) as archive:
        assert archive.namelist() == ["main.py"]


def test_tools_require_reads_never_overwrite_and_invalidate_checks():
    async def scenario():
        tools = CodingTools({"main.py": "value = 1\n"}, FakeSandbox(), uuid4().hex)
        async def use(name, **args):
            return await tools.execute(name, json.dumps(args))
        assert not (await use("write_file", path="main.py", content="overwrite"))["ok"]
        assert not (await use("replace_text", path="main.py", old_text="1", new_text="2"))["ok"]
        await use("read_file", path="main.py")
        await use("run_check", kind="syntax")
        assert (await use("replace_text", path="main.py", old_text="1", new_text="2"))["ok"]
        assert tools.files["main.py"] == "value = 2\n"
        assert tools.checks[0]["stale"] is True
        assert not (await use("write_file", path="../outside.py", content="pass"))["ok"]
        assert not (await use("run_check", kind="shell"))["ok"]
        assert not (await use("shell", command="echo nope"))["ok"]
    asyncio.run(scenario())


def imported_project(logged_in, app):
    owner = logged_in.get("/api/auth/me").json()["user"]["id"]
    store = CodingStore(app.state.auth_store)
    return store.create_project(owner, "demo", {"main.py": "value = 1\n"}, [])["id"]


def test_owner_snapshot_idempotency_and_continuation(logged_in, app):
    user_id = logged_in.get("/api/auth/me").json()["user"]["id"]
    project_id = imported_project(logged_in, app)
    store = CodingStore(app.state.auth_store)
    data = CreateTask(instruction="Change value", client_task_id=uuid4())
    task, replayed = store.create_task(user_id, project_id, data)
    assert not replayed
    assert store.create_task(user_id, project_id, data)[1] is True
    with pytest.raises(CodingError):
        store.get_task(user_id + 10000, task["id"])
    with pytest.raises(CodingError):
        store.delete_project(user_id, project_id)
    assert logged_in.get(f"/api/coding/tasks/{task['id']}/export").status_code == 404
    store.save_task(user_id, task["id"], files={"main.py": "value = 2\n"}, status="completed")
    assert store.get_project(user_id, project_id)["source_files"]["main.py"] == "value = 1\n"
    assert public_task(store.get_task(user_id, task["id"]))["changes"][0]["status"] == "modified"
    again, _ = store.create_task(user_id, project_id, CreateTask(
        instruction="Continue", client_task_id=uuid4(), base_task_id=task["id"],
    ))
    assert again["files"]["main.py"] == "value = 2\n"
    store.request_cancel(user_id, again["id"])
    assert store.is_cancelled(user_id, again["id"])
    store.save_task(user_id, again["id"], status="cancelled")
    assert store.save_task(user_id, again["id"], status="running")["status"] == "cancelled"
    assert logged_in.get(f"/api/coding/tasks/{task['id']}/export").status_code == 404


def test_tool_loop_persists_real_diff_and_shared_usage_without_external_calls(logged_in, app):
    user_id = logged_in.get("/api/auth/me").json()["user"]["id"]
    project_id = imported_project(logged_in, app)
    store = CodingStore(app.state.auth_store)
    service = CodingService(store, app.state.model_calls, app.state.settings)

    class Model:
        name = "fake-coding"
        calls = 0

        async def call(self, messages):
            self.calls += 1
            if self.calls == 1:
                name, args = "read_file", {"path": "main.py"}
            elif self.calls == 2:
                assert messages[-1]["role"] == "tool"
                name, args = "replace_text", {"path": "main.py", "old_text": "1", "new_text": "2"}
            else:
                return CodingReply("已修改。", [], 10, 5)
            return CodingReply("", [{"id": uuid4().hex, "type": "function", "function": {
                "name": name, "arguments": json.dumps(args),
            }}], 10, 5)

    service.model, service.sandbox = Model(), FakeSandbox()
    task, _ = store.create_task(user_id, project_id, CreateTask(instruction="Change value", client_task_id=uuid4()))
    asyncio.run(service._run(user_id, task["id"]))
    result = store.get_task(user_id, task["id"])
    assert result["status"] == "completed", result["error"]
    assert result["files"]["main.py"] == "value = 2\n"
    assert result["checks"][0]["kind"] == "syntax"
    assert result["checks"][0]["stale"] is False
    assert app.state.model_calls.usage(user_id, app.state.settings)["requests"] == 3


def test_cancelled_loop_releases_model_reservation(logged_in, app):
    user_id = logged_in.get("/api/auth/me").json()["user"]["id"]
    project_id = imported_project(logged_in, app)
    store = CodingStore(app.state.auth_store)
    service = CodingService(store, app.state.model_calls, app.state.settings)
    task, _ = store.create_task(user_id, project_id, CreateTask(instruction="Wait", client_task_id=uuid4()))

    async def scenario():
        entered = asyncio.Event()

        class Model:
            name = "fake-waiting"

            async def call(self, messages):
                entered.set()
                await asyncio.Event().wait()

        service.model = Model()
        service.launch(user_id, task["id"])
        worker = service.tasks[task["id"]]
        await asyncio.wait_for(entered.wait(), 2)
        await service.cancel(user_id, task["id"])
        await asyncio.wait_for(worker, 2)
    asyncio.run(scenario())
    assert store.get_task(user_id, task["id"])["status"] == "cancelled"
    with app.state.auth_store.connect() as database:
        assert database.execute("SELECT count(*) FROM model_calls WHERE state = 'running'").fetchone()[0] == 0


def test_retirement_preserves_legacy_source_and_result(logged_in, app):
    owner = logged_in.get("/api/auth/me").json()["user"]["id"]
    project_id = imported_project(logged_in, app)
    store = CodingStore(app.state.auth_store)
    task, _ = store.create_task(owner, project_id, CreateTask(instruction="Old task", client_task_id=uuid4()))
    store.save_task(owner, task["id"], files={"main.py": "value = 2\n"}, status="running")
    assert retire_zip_tasks(app.state.auth_store) == 1
    assert retire_zip_tasks(app.state.auth_store) == 0
    archived = store.get_task(owner, task["id"])
    assert archived["status"] == "interrupted"
    assert archived["files"] == {"main.py": "value = 2\n"}
    assert archived["baseline_files"] == {"main.py": "value = 1\n"}
    assert store.get_project(owner, project_id)["source_files"] == {"main.py": "value = 1\n"}
