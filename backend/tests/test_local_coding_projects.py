"""Offline regression source for user-run validation; no model or user files are used."""

import sqlite3
from uuid import UUID, uuid4

import pytest

from app.coding.local_schemas import LocalConversationCreate, LocalProjectCreate, LocalProjectRename
from app.coding.local_service import LOCAL_PROMPT
from app.coding.model import CodingReply
from app.coding.schemas import CodingError
from app.coding.store import encode
from app.migrations import SQL_DIR, initialize_database
from tests.test_local_coding import create_task


def test_same_project_has_independent_conversations_and_shared_directory(logged_in, app):
    owner, data, first, store = create_task(logged_in, app)
    project_id = store.get_conversation(owner, first["conversation_id"])["project_id"]
    store.claim(owner, first["id"], 12)
    store.reply(owner, first["id"], CodingReply("private-dialog-a-context", []))
    second = store.create_conversation(owner, LocalConversationCreate(
        client_conversation_id=uuid4(), project_id=project_id, directory_name="demo"))
    assert second["project_id"] == project_id
    assert store.get_conversation(owner, second["id"])["tasks"] == []
    turn = store.create(owner, data.model_copy(update={
        "client_task_id": uuid4(), "conversation_id": UUID(second["id"]),
        "instruction": "Read the latest project files", "paths": ["current.py"],
    }), app.state.settings, LOCAL_PROMPT)
    assert turn["turn_index"] == 1
    assert "private-dialog-a-context" not in encode(turn["messages"])
    assert "current.py" in turn["messages"][-1]["content"]
    store.stop(owner, turn["id"])
    store.remove_conversation(owner, first["conversation_id"])
    assert store.list_projects(owner)[0]["id"] == project_id
    assert store.get_conversation(owner, second["id"])["turn_count"] == 1


def test_project_ownership_replay_and_name_checks(logged_in, app):
    owner = logged_in.get("/api/auth/me").json()["user"]["id"]
    store = app.state.local_coding.store
    request = LocalProjectCreate(client_project_id=uuid4(), directory_name="demo")
    project = store.create_project(owner, request)
    assert store.create_project(owner, request) == project
    with pytest.raises(CodingError) as conflict:
        store.create_project(owner, request.model_copy(update={"directory_name": "other"}))
    assert conflict.value.status == 409
    # A name alone cannot identify a directory: same-name projects stay separate.
    other = store.create_project(owner, LocalProjectCreate(client_project_id=uuid4(), directory_name="demo"))
    assert other["id"] != project["id"]
    assert store.list_projects(owner + 1000) == []
    create = LocalConversationCreate(client_conversation_id=uuid4(), project_id=project["id"], directory_name="demo")
    with pytest.raises(CodingError) as denied:
        store.create_conversation(owner + 1000, create)
    assert denied.value.status == 404
    with pytest.raises(CodingError) as denied:
        store.remove_project(owner + 1000, project["id"])
    assert denied.value.status == 404
    with pytest.raises(CodingError):
        store.create_conversation(owner, create.model_copy(update={"directory_name": "wrong"}))
    conversation = store.create_conversation(owner, create)
    assert store.create_conversation(owner, create) == conversation
    with pytest.raises(CodingError):
        store.create_conversation(owner, create.model_copy(update={"project_id": UUID(other["id"])}))


def test_active_project_cannot_be_deleted_and_other_dialog_cannot_execute(logged_in, app):
    owner, data, task, store = create_task(logged_in, app)
    project_id = store.get_conversation(owner, task["conversation_id"])["project_id"]
    another = store.create_conversation(owner, LocalConversationCreate(
        client_conversation_id=uuid4(), project_id=project_id, directory_name="demo"))
    with pytest.raises(CodingError) as busy:
        store.remove_project(owner, project_id)
    assert busy.value.status == 409
    with pytest.raises(CodingError) as busy:
        store.create(owner, data.model_copy(update={
            "conversation_id": UUID(another["id"]), "client_task_id": uuid4(),
        }), app.state.settings, LOCAL_PROMPT)
    assert busy.value.status == 409
    store.stop(owner, task["id"])
    store.remove_project(owner, project_id)
    assert store.list_projects(owner) == []
    assert store.list_conversations(owner) == []
    assert store.list(owner) == []


def test_project_http_contract_and_create_after_last_conversation_deleted(logged_in):
    response = logged_in.post("/api/coding/local/projects", json={
        "client_project_id": str(uuid4()), "directory_name": "demo",
    })
    assert response.status_code == 201
    project = response.json()
    assert set(project) == {"id", "name", "directory_name", "created_at", "updated_at"}
    assert project["name"] == "项目 1"
    assert logged_in.get("/api/coding/local/projects").json()["items"] == [project]
    for _ in range(2):
        created = logged_in.post("/api/coding/local/conversations", json={
            "client_conversation_id": str(uuid4()), "project_id": project["id"], "directory_name": "demo",
        })
        assert created.status_code == 201
        assert created.json()["project_id"] == project["id"]
        assert logged_in.delete(f"/api/coding/local/conversations/{created.json()['id']}").status_code == 204
    assert logged_in.delete(f"/api/coding/local/projects/{project['id']}").status_code == 204
    assert logged_in.delete(f"/api/coding/local/projects/{project['id']}").status_code == 404


def test_v9_migration_keeps_same_name_projects_separate_and_is_repeatable(tmp_path):
    path = tmp_path / "project-upgrade.sqlite3"
    prefix = (SQL_DIR / "schema.sql").read_text(encoding="utf-8").split("-- Browser-local tasks")[0]
    ids = [uuid4().hex, uuid4().hex]
    with sqlite3.connect(path) as db:
        db.executescript(prefix)
        db.executescript((SQL_DIR / "migrations/007_local_coding.sql").read_text(encoding="utf-8"))
        db.executescript((SQL_DIR / "migrations/008_local_coding_conversations.sql").read_text(encoding="utf-8"))
        db.execute("INSERT INTO users(username,password_hash,created_at) VALUES ('kept-user','hash',1)")
        owner = db.execute("SELECT id FROM users").fetchone()[0]
        db.executemany("INSERT INTO schema_migrations VALUES (?,1)", [(version,) for version in range(1, 10)])
        for identifier in ids:
            db.execute("""INSERT INTO local_coding_conversations
                (id,user_id,client_conversation_id,directory_name,title,created_at,updated_at)
                VALUES(?,?,?,'same-name','kept-title',1,2)""", (identifier, owner, identifier))
    initialize_database(path)
    initialize_database(path)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT max(version) FROM schema_migrations").fetchone()[0] == 13
        assert {row[0] for row in db.execute("SELECT name FROM local_coding_projects")} == {"same-name"}
        assert {row[0] for row in db.execute("SELECT id FROM local_coding_projects")} == set(ids)
        assert db.execute("SELECT count(*) FROM local_coding_conversations WHERE id=project_id AND title='kept-title'").fetchone()[0] == 2
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("UPDATE local_coding_conversations SET project_id=NULL WHERE id=?", (ids[0],))
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("UPDATE local_coding_conversations SET project_id=? WHERE id=?", (ids[1], ids[0]))


def test_default_names_are_persistent_sequential_and_replays_do_not_consume_numbers(logged_in, app):
    owner = logged_in.get("/api/auth/me").json()["user"]["id"]
    store = app.state.local_coding.store
    request = LocalProjectCreate(client_project_id=uuid4(), directory_name="demo", name="  ")
    first = store.create_project(owner, request)
    assert first["name"] == "项目 1"
    assert store.create_project(owner, request)["id"] == first["id"]
    named = store.create_project(owner, LocalProjectCreate(client_project_id=uuid4(), directory_name="folder", name=" 我的主页 "))
    assert named["name"] == "我的主页" and named["directory_name"] == "folder"
    store.rename_project(owner, first["id"], LocalProjectRename(name="已改名"))
    # Creation retries still compare against the original request, not the later display name.
    assert store.create_project(owner, request)["name"] == "已改名"
    with pytest.raises(CodingError):
        store.create_project(owner, request.model_copy(update={"name": "different request"}))
    store.remove_project(owner, first["id"])
    # Use another store instance: numbering is in SQLite, not memory or remaining project count.
    other_store = type(store)(store.auth)
    second = other_store.create_project(owner, LocalProjectCreate(client_project_id=uuid4(), directory_name="demo"))
    assert second["name"] == "项目 2"
    store.create_project(owner, LocalProjectCreate(client_project_id=uuid4(), directory_name="folder", name="项目 3"))
    fourth = store.create_project(owner, LocalProjectCreate(client_project_id=uuid4(), directory_name="demo"))
    assert fourth["name"] == "项目 4"
    renamed = store.rename_project(owner, named["id"], LocalProjectRename(name=" "))
    assert renamed["name"] == "项目 5"


def test_rename_is_owned_and_does_not_rebind_conversations(logged_in, app):
    owner, data, task, store = create_task(logged_in, app)
    before = store.get_conversation(owner, task["conversation_id"])
    identifier = before["project_id"]
    with pytest.raises(CodingError) as denied:
        store.rename_project(owner + 1000, identifier, LocalProjectRename(name="not allowed"))
    assert denied.value.status == 404
    response = logged_in.patch(f"/api/coding/local/projects/{identifier}", json={"name": "我的网站"})
    assert response.status_code == 200
    assert response.json()["name"] == "我的网站"
    assert response.json()["directory_name"] == "demo"
    assert store.get_conversation(owner, task["conversation_id"]) == before
    assert logged_in.patch(f"/api/coding/local/projects/{identifier}", json={"name": "x" * 121}).status_code == 422
    assert logged_in.patch(f"/api/coding/local/projects/{identifier}", json={}).status_code == 422
    assert logged_in.patch(f"/api/coding/local/projects/{identifier}", json={"name": "a\u0000b"}).status_code == 422
    assert logged_in.patch(f"/api/coding/local/projects/{uuid4().hex}", json={"name": "missing"}).status_code == 404


def test_v10_name_migration_keeps_ids_and_original_directory(tmp_path):
    path = tmp_path / "name-upgrade.sqlite3"
    prefix = (SQL_DIR / "schema.sql").read_text(encoding="utf-8").split("-- Browser-local tasks")[0]
    identifier = uuid4().hex
    with sqlite3.connect(path) as db:
        db.executescript(prefix)
        for filename in ("007_local_coding.sql", "008_local_coding_conversations.sql", "010_local_coding_projects.sql"):
            db.executescript((SQL_DIR / "migrations" / filename).read_text(encoding="utf-8"))
        db.execute("INSERT INTO users(username,password_hash,created_at) VALUES ('kept-user','hash',1)")
        owner = db.execute("SELECT id FROM users").fetchone()[0]
        db.executemany("INSERT INTO schema_migrations VALUES (?,1)", [(version,) for version in range(1, 11)])
        db.execute("""INSERT INTO local_coding_projects(id,user_id,client_project_id,directory_name,created_at,updated_at)
            VALUES(?,?,?,'original-folder',1,2)""", (identifier, owner, identifier))
    initialize_database(path)
    initialize_database(path)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT id,name,directory_name,name_request FROM local_coding_projects").fetchone() == (
            identifier, "original-folder", "original-folder", "")
        assert db.execute("SELECT max(version) FROM schema_migrations").fetchone()[0] == 13
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
