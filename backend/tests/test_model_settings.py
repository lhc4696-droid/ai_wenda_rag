"""Model settings regression cases. No external model/DNS calls are required."""

from dataclasses import replace
import time

from cryptography.fernet import Fernet
import pytest

from app.model_settings.schemas import ModelSettingsError
from app.model_settings.store import ModelSettingsStore


# Explicitly fake test-only credentials; never use these with a live service.
FAKE_KEY = "test-only-model-key-not-valid-for-any-service"
PROFILE = {
    "enabled": True,
    "base_url": "https://models.example.com/v1",
    "api_key": FAKE_KEY,
    "chat_model": "example-chat",
    "coding_model": "",
    "vision_model": "",
}


@pytest.fixture
def configured_models(app, logged_in):
    settings = replace(app.state.settings, model_config_secret=Fernet.generate_key().decode("ascii"))
    store = ModelSettingsStore(app.state.auth_store, settings)
    app.state.model_settings = store
    owner = logged_in.get("/api/auth/me").json()["user"]["id"]
    return store, owner


def test_credentials_encrypted_and_never_returned(configured_models, logged_in, app):
    store, owner = configured_models
    response = logged_in.patch("/api/model-settings", json=PROFILE)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["has_api_key"] is True
    assert "api_key" not in response.json()
    assert FAKE_KEY not in response.text
    assert FAKE_KEY not in logged_in.get("/api/model-settings").text
    with app.state.auth_store.connect() as db:
        row = db.execute("SELECT * FROM user_model_settings WHERE user_id = ?", (owner,)).fetchone()
        assert FAKE_KEY not in str(dict(row))
        assert row["api_key_encrypted"]
    profile = store.resolve(owner)
    assert profile.api_key == FAKE_KEY
    assert profile.coding_model == PROFILE["chat_model"]
    assert FAKE_KEY not in repr(profile)


def test_blank_key_preserved_but_destination_change_requires_new_key(configured_models, logged_in):
    store, owner = configured_models
    assert logged_in.patch("/api/model-settings", json=PROFILE).status_code == 200
    assert logged_in.patch("/api/model-settings", json={"api_key": "", "chat_model": "example-other"}).status_code == 200
    assert store.resolve(owner).api_key == FAKE_KEY
    rejected = logged_in.patch("/api/model-settings", json={"base_url": "https://other.example.com/v1"})
    assert rejected.status_code == 400
    assert store.resolve(owner).base_url == PROFILE["base_url"]
    assert logged_in.patch("/api/model-settings", json={
        "base_url": "https://other.example.com/v1", "api_key": "another-fake-test-only-key",
    }).status_code == 200
    assert store.resolve(owner).base_url == "https://other.example.com/v1"


def test_disable_retains_key_and_delete_removes_account_configuration(configured_models, logged_in):
    store, owner = configured_models
    store.update(owner, PROFILE)
    disabled = logged_in.patch("/api/model-settings", json={"enabled": False})
    assert disabled.status_code == 200
    assert disabled.json()["has_api_key"] is True
    assert store.resolve(owner) is None
    assert logged_in.patch("/api/model-settings", json={"enabled": True}).status_code == 200
    assert store.resolve(owner).api_key == FAKE_KEY
    deleted = logged_in.delete("/api/model-settings")
    assert deleted.status_code == 200
    assert deleted.json()["has_api_key"] is False
    assert deleted.json()["enabled"] is False
    assert deleted.json()["base_url"] == ""


def test_disabled_default_works_without_encryption_secret(app, logged_in):
    result = logged_in.get("/api/model-settings")
    assert result.status_code == 200
    assert result.json()["storage_ready"] is False
    assert result.json()["enabled"] is False
    assert logged_in.patch("/api/model-settings", json=PROFILE).status_code == 503
    assert logged_in.patch("/api/model-settings", json={"enabled": False}).status_code == 200


def test_replaced_encryption_secret_never_falls_back_to_server_model(configured_models, app):
    store, owner = configured_models
    store.update(owner, PROFILE)
    changed = ModelSettingsStore(app.state.auth_store, replace(
        store.settings, model_config_secret=Fernet.generate_key().decode("ascii"),
    ))
    with pytest.raises(ModelSettingsError) as caught:
        changed.resolve(owner)
    assert caught.value.code == "MODEL_KEY_UNREADABLE"
    assert FAKE_KEY not in str(caught.value)
    changed.update(owner, {"enabled": False})
    assert changed.resolve(owner) is None
    changed.update(owner, {"enabled": True, "api_key": "replacement-fake-only-key"})
    assert changed.resolve(owner).api_key == "replacement-fake-only-key"


def test_other_accounts_cannot_resolve_or_delete_owner_configuration(configured_models, app):
    store, owner = configured_models
    store.update(owner, PROFILE)
    with app.state.auth_store.connect() as db:
        other = db.execute(
            "INSERT INTO users(username,password_hash,created_at) VALUES (?,?,?)",
            ("other_models_owner", "not-a-real-password-hash", int(time.time())),
        ).lastrowid
    assert store.resolve(other) is None
    assert store.public(other)["has_api_key"] is False
    store.delete(other)
    assert store.resolve(owner).api_key == FAKE_KEY
    store.update(other, PROFILE)
    # Even an accidental row-to-row ciphertext copy must fail owner binding.
    with app.state.auth_store.connect() as db:
        db.execute("UPDATE user_model_settings SET api_key_encrypted = (SELECT api_key_encrypted FROM user_model_settings WHERE user_id = ?) WHERE user_id = ?", (owner, other))
    with pytest.raises(ModelSettingsError) as caught:
        store.resolve(other)
    assert caught.value.code == "MODEL_KEY_UNREADABLE"


def test_active_model_request_blocks_settings_mutation(configured_models, logged_in, app):
    store, owner = configured_models
    store.update(owner, PROFILE)
    now = int(time.time())
    with app.state.auth_store.connect() as db:
        db.execute(
            "INSERT INTO model_calls(request_id,user_id,model,created_at,lease_expires_at,state,input_chars) VALUES (?,?,?,?,?,'running',1)",
            ("settings-busy-test", owner, "test-model", now, now + 60),
        )
    assert logged_in.patch("/api/model-settings", json={"enabled": False}).status_code == 409
    assert logged_in.delete("/api/model-settings").status_code == 409
    assert store.resolve(owner).api_key == FAKE_KEY


def test_database_destination_change_cannot_reuse_saved_key(configured_models, app):
    store, owner = configured_models
    store.update(owner, PROFILE)
    with app.state.auth_store.connect() as db:
        db.execute("UPDATE user_model_settings SET base_url = ? WHERE user_id = ?", ("https://changed.example.com/v1", owner))
    with pytest.raises(ModelSettingsError) as caught:
        store.resolve(owner)
    assert caught.value.code == "MODEL_KEY_UNREADABLE"
    assert FAKE_KEY not in str(caught.value)


@pytest.mark.parametrize("status", ["queued", "running", "waiting_tools"])
@pytest.mark.parametrize("deadline_offset", [60, -1])
def test_only_active_coding_turn_blocks_settings_mutation(configured_models, logged_in, app, status, deadline_offset):
    store, owner = configured_models
    store.update(owner, PROFILE)
    now = int(time.time())
    with app.state.auth_store.connect() as db:
        db.execute(
            "INSERT INTO local_coding_projects(id,user_id,client_project_id,directory_name,created_at,updated_at) VALUES (?,?,?,?,?,?)",
            ("model-setting-project", owner, "model-setting-project", "example", now, now),
        )
        db.execute(
            "INSERT INTO local_coding_conversations(id,user_id,project_id,client_conversation_id,directory_name,title,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)",
            ("model-setting-conversation", owner, "model-setting-project", "model-setting-conversation", "example", "Example", now, now),
        )
        db.execute(
            """INSERT INTO local_coding_tasks
               (id,user_id,conversation_id,turn_index,client_task_id,grant_hash,request_hash,
                directory_name,instruction,status,created_at,updated_at,deadline_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("model-setting-task", owner, "model-setting-conversation", 1, "model-setting-task",
             "unused-test-hash", "unused-test-hash", "example", "test instruction", status, now, now, now + deadline_offset),
        )
    expected = 409 if deadline_offset > 0 else 200
    assert logged_in.patch("/api/model-settings", json={"enabled": False}).status_code == expected
    assert logged_in.delete("/api/model-settings").status_code == expected
    if deadline_offset > 0:
        assert store.resolve(owner).api_key == FAKE_KEY
    else:
        assert store.resolve(owner) is None


@pytest.mark.parametrize("base_url", [
    "http://models.example.com/v1", "https://127.0.0.1/v1", "https://localhost/v1",
    "https://169.254.169.254/v1", "https://user:password@example.com/v1",
    "https://models.example.com/v1?api_key=hidden", "https://models.example.com/v1/chat/completions",
])
def test_invalid_destinations_rejected_without_network(configured_models, logged_in, base_url):
    response = logged_in.patch("/api/model-settings", json={**PROFILE, "base_url": base_url})
    assert response.status_code == 400
    assert FAKE_KEY not in response.text


@pytest.mark.parametrize("changes", [
    {"user_id": 99}, {"api_key": "secret\r\ninjected-header"},
    {"api_key": None}, {"enabled": "true"}, {"chat_model": "model\nname"},
])
def test_invalid_patch_does_not_echo_credentials(configured_models, logged_in, changes):
    response = logged_in.patch("/api/model-settings", json={**PROFILE, **changes})
    assert response.status_code == 422
    assert FAKE_KEY not in response.text
    assert "injected-header" not in response.text


def test_routes_require_login_and_reject_cross_site(client):
    assert client.get("/api/model-settings").status_code == 401
    assert client.patch("/api/model-settings", json=PROFILE).status_code == 401
    assert client.delete("/api/model-settings").status_code == 401
    assert client.patch("/api/model-settings", json=PROFILE, headers={"Origin": "https://evil.example"}).status_code == 403
