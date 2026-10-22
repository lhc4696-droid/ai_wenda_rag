"""Offline connection-test regressions; all provider calls are explicit fakes."""

import asyncio
from dataclasses import replace
import json
import threading
import time

from cryptography.fernet import Fernet
import pytest

from app.integrations.llm import LLMError
from app.model_settings.connection_test import CHAT_PROMPT, PROBE_SYSTEM, run_connection_test
from app.model_settings.schemas import ModelProfile, ModelSettingsError
from app.model_settings.store import ModelSettingsStore


FAKE_KEY = "connection-test-fake-key-never-use-live"
DRAFT = {
    "target": "chat", "base_url": "https://models.example.com/v1",
    "api_key": FAKE_KEY, "chat_model": "example-chat", "coding_model": "example-coder",
}


def completion(content="OK", *, reason="stop", calls=None):
    message = {"role": "assistant", "content": content}
    if calls is not None:
        message["tool_calls"] = calls
    return {"choices": [{"message": message, "finish_reason": reason}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 3}}


def tool_call(arguments='{"ok":true}'):
    return {"id": "call_test", "type": "function",
            "function": {"name": "connection_probe", "arguments": arguments}}


@pytest.fixture
def encrypted_models(app, logged_in):
    settings = replace(app.state.settings, model_config_secret=Fernet.generate_key().decode("ascii"))
    store = ModelSettingsStore(app.state.auth_store, settings)
    app.state.model_settings = store
    owner = logged_in.get("/api/auth/me").json()["user"]["id"]
    return store, owner


def install_reply(monkeypatch, reply=None, error=None):
    observed = []

    async def fake(base_url, api_key, payload, timeout, **network_options):
        observed.append((base_url, api_key, payload, timeout))
        if error is not None:
            raise error
        return completion() if reply is None else reply

    monkeypatch.setattr("app.model_settings.connection_test.post_completion", fake)
    return observed


def test_draft_probe_without_storage_does_not_save_or_send_history(app, logged_in, monkeypatch):
    observed = install_reply(monkeypatch)
    assert app.state.model_settings.public(1)["storage_ready"] is False
    response = logged_in.post("/api/model-settings/test", json=DRAFT)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["ok"] is True
    assert result["target"] == "chat"
    assert result["model"] == DRAFT["chat_model"]
    assert result["error_code"] is None
    assert result["elapsed_ms"] >= 0
    assert result["input_tokens"] == 10
    assert result["output_tokens"] == 3
    assert result["request_id"] == response.headers["x-request-id"]
    assert response.headers["cache-control"] == "no-store"
    assert FAKE_KEY not in response.text
    assert observed[0][0:2] == (DRAFT["base_url"], FAKE_KEY)
    payload = observed[0][2]
    assert payload["messages"] == [
        {"role": "system", "content": PROBE_SYSTEM}, {"role": "user", "content": CHAT_PROMPT},
    ]
    assert payload["max_tokens"] <= 128
    assert "tools" not in payload
    assert FAKE_KEY not in json.dumps(payload)
    assert observed[0][3] <= 20
    with app.state.auth_store.connect() as db:
        assert db.execute("SELECT count(*) FROM user_model_settings").fetchone()[0] == 0
        row = db.execute("SELECT * FROM model_calls WHERE request_id = ?", (result["request_id"],)).fetchone()
        assert row["model"] == DRAFT["chat_model"]
        assert row["state"] == "succeeded"
        assert row["input_tokens"] == 10
        assert row["output_tokens"] == 3


def test_saved_disabled_key_can_be_tested_without_enabling_or_changing_settings(encrypted_models, logged_in, monkeypatch):
    store, owner = encrypted_models
    before = store.update(owner, {
        "enabled": False, "base_url": DRAFT["base_url"], "api_key": FAKE_KEY,
        "chat_model": "saved-chat", "coding_model": "saved-coder",
    })
    observed = install_reply(monkeypatch)
    response = logged_in.post("/api/model-settings/test", json={
        **DRAFT, "api_key": "", "base_url": "https://MODELS.example.com:443/v1/",
    })
    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert observed[0][1] == FAKE_KEY
    assert observed[0][2]["model"] == "example-chat"
    assert store.public(owner) == before
    assert store.resolve(owner) is None


def test_changed_destination_requires_new_key_before_decrypt(encrypted_models, logged_in, monkeypatch):
    store, owner = encrypted_models
    store.update(owner, {key: value for key, value in DRAFT.items() if key != "target"})

    def must_not_decrypt(*args):
        raise AssertionError("changed URL must fail before decryption")

    monkeypatch.setattr(store, "_decrypt", must_not_decrypt)
    observed = install_reply(monkeypatch)
    response = logged_in.post("/api/model-settings/test", json={
        **DRAFT, "base_url": "https://other.example.com/v1", "api_key": "",
    })
    assert response.status_code == 400
    assert response.headers["x-error-code"] == "MODEL_KEY_REQUIRED_FOR_URL_CHANGE"
    assert observed == []


def test_saved_key_is_never_resolved_for_another_owner(encrypted_models, app):
    store, owner = encrypted_models
    store.update(owner, {key: value for key, value in DRAFT.items() if key != "target"})
    with app.state.auth_store.connect() as db:
        other = db.execute("INSERT INTO users(username,password_hash,created_at) VALUES (?,?,?)",
                           ("connection_other_owner", "fake-hash", int(time.time()))).lastrowid
    with pytest.raises(ModelSettingsError) as caught:
        store.preview(other, {**DRAFT, "api_key": ""})
    assert caught.value.code == "MODEL_KEY_REQUIRED"


@pytest.mark.parametrize("extra", [
    {"user_id": 42}, {"messages": [{"role": "user", "content": "secret"}]},
    {"enabled": True}, {"target": "vision"}, {"api_key": None},
    {"api_key": "key\r\nX-Header: secret"}, {"chat_model": "   "},
])
def test_draft_rejects_extra_or_invalid_fields_without_echo(logged_in, monkeypatch, extra):
    observed = install_reply(monkeypatch)
    response = logged_in.post("/api/model-settings/test", json={**DRAFT, **extra})
    assert response.status_code == 422
    assert FAKE_KEY not in response.text
    assert "X-Header" not in response.text
    assert observed == []


@pytest.mark.parametrize("base_url", ["https://127.0.0.1/v1", "https://169.254.169.254/v1", "http://models.example.com/v1"])
def test_unsafe_endpoint_is_rejected_before_probe(logged_in, monkeypatch, base_url):
    observed = install_reply(monkeypatch)
    assert logged_in.post("/api/model-settings/test", json={**DRAFT, "base_url": base_url}).status_code == 400
    assert observed == []


def test_probe_requires_login_and_trusted_origin(client, logged_in, monkeypatch):
    observed = install_reply(monkeypatch)
    assert logged_in.post("/api/model-settings/test", json=DRAFT,
                          headers={"Origin": "https://evil.example"}).status_code == 403
    client.cookies.clear()
    assert client.post("/api/model-settings/test", json=DRAFT).status_code == 401
    assert observed == []


def test_coding_probe_checks_function_call_but_never_executes_tools(logged_in, monkeypatch):
    observed = install_reply(monkeypatch, completion(None, reason="tool_calls", calls=[tool_call()]))
    result = logged_in.post("/api/model-settings/test", json={**DRAFT, "target": "coding"}).json()
    assert result["ok"] is True
    assert result["model"] == "example-coder"
    payload = observed[0][2]
    assert payload["tool_choice"] == "auto"
    assert payload["parallel_tool_calls"] is False
    assert [item["function"]["name"] for item in payload["tools"]] == ["connection_probe"]
    assert len(observed) == 1


@pytest.mark.parametrize("reply", [
    completion("I can use tools"),
    completion(None, calls=[tool_call('{"ok":1}')]),
    completion(None, calls=[tool_call('{"ok":true,"path":"private"}')]),
    completion(None, calls=[tool_call(), tool_call()]),
    completion(None, calls=[{**tool_call(), "id": ""}]),
    completion(None, calls=[{**tool_call(), "function": {"name": "read_file", "arguments": "{}"}}]),
])
def test_coding_chat_text_or_invalid_calls_are_not_false_success(logged_in, monkeypatch, reply):
    observed = install_reply(monkeypatch, reply)
    response = logged_in.post("/api/model-settings/test", json={**DRAFT, "target": "coding", "coding_model": ""})
    assert response.status_code == 200
    assert response.json()["ok"] is False
    assert response.json()["error_code"] == "MODEL_TEST_TOOLS_UNSUPPORTED"
    assert response.json()["model"] == DRAFT["chat_model"]
    assert len(observed) == 1


@pytest.mark.parametrize("reply,code", [
    (completion(""), "MODEL_TEST_CHAT_INVALID"),
    (completion("OK", reason="length"), "MODEL_TEST_TRUNCATED"),
    (completion("No", reason="content_filter"), "MODEL_TEST_REFUSED"),
    ({"choices": []}, "LLM_INVALID_RESPONSE"),
])
def test_invalid_chat_response_reports_failure_and_releases_quota(app, logged_in, monkeypatch, reply, code):
    install_reply(monkeypatch, reply)
    result = logged_in.post("/api/model-settings/test", json=DRAFT).json()
    assert result["ok"] is False
    assert result["error_code"] == code
    with app.state.auth_store.connect() as db:
        row = db.execute("SELECT state,error_code FROM model_calls WHERE request_id = ?", (result["request_id"],)).fetchone()
        assert row["state"] == "failed"
        assert row["error_code"] == code


@pytest.mark.parametrize("error,code", [
    (LLMError("API Key 或权限有误", "LLM_AUTH_FAILED"), "LLM_AUTH_FAILED"),
    (LLMError("模型响应超时", "LLM_TIMEOUT", 504), "LLM_TIMEOUT"),
    (RuntimeError("accidentally contains " + FAKE_KEY), "INTERNAL_ERROR"),
])
def test_failure_is_safe_bounded_and_not_retried(logged_in, monkeypatch, error, code):
    observed = install_reply(monkeypatch, error=error)
    response = logged_in.post("/api/model-settings/test", json=DRAFT)
    assert response.status_code == 200
    assert response.json()["ok"] is False
    assert response.json()["error_code"] == code
    assert FAKE_KEY not in response.text
    assert "accidentally" not in response.text
    assert len(observed) == 1


def test_connection_test_uses_shared_rate_limits(app, logged_in, monkeypatch):
    app.state.settings = replace(app.state.settings, chat=replace(app.state.settings.chat, requests_per_minute=1))
    observed = install_reply(monkeypatch)
    assert logged_in.post("/api/model-settings/test", json=DRAFT).json()["ok"] is True
    limited = logged_in.post("/api/model-settings/test", json=DRAFT)
    assert limited.status_code == 429
    assert limited.headers["x-error-code"] == "CHAT_RATE_LIMIT"
    assert "retry-after" in limited.headers
    assert len(observed) == 1


@pytest.mark.parametrize('target', ['chat', 'coding'])
def test_personal_connection_probe_does_not_spend_default_daily_quota(app, logged_in, monkeypatch, target):
    settings = replace(app.state.settings, chat=replace(app.state.settings.chat, requests_per_day=1))
    app.state.settings = settings
    owner = logged_in.get('/api/auth/me').json()['user']['id']
    app.state.model_calls.reserve('default-used', owner, 1, settings)
    app.state.model_calls.finish('default-used', 'succeeded', 1)
    install_reply(monkeypatch, completion(None, reason='tool_calls', calls=[tool_call()]) if target == 'coding' else completion())
    result = logged_in.post('/api/model-settings/test', json={**DRAFT, 'target': target})
    assert result.status_code == 200 and result.json()['ok'], result.text
    usage = logged_in.get('/api/chat/usage').json()
    assert usage['requests'] == 1 and usage['custom']['requests'] == 1


def test_cancellation_drains_in_progress_reservation_and_finishes(app):
    entered, release = threading.Event(), threading.Event()
    finishes = []

    class Store:
        def reserve(self, *args):
            entered.set()
            assert release.wait(2)

        def finish(self, *args, **kwargs):
            finishes.append((args, kwargs))

    profile = ModelProfile(DRAFT["base_url"], FAKE_KEY, "chat", "coding")

    async def scenario():
        task = asyncio.create_task(run_connection_test(Store(), app.state.settings, profile, "chat", 1, "cancel-probe"))
        assert await asyncio.to_thread(entered.wait, 2)
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    assert len(finishes) == 1
    assert finishes[0][0][0:2] == ("cancel-probe", "cancelled")
    assert finishes[0][1]["error_code"] == "REQUEST_CANCELLED"


def test_client_disconnect_stops_probe_and_releases_lease(app, monkeypatch):
    finishes = []

    class Store:
        def reserve(self, *args):
            pass

        def finish(self, *args, **kwargs):
            finishes.append((args, kwargs))

    async def pending(*args, **kwargs):
        await asyncio.Event().wait()

    async def disconnected():
        return True

    monkeypatch.setattr("app.model_settings.connection_test.post_completion", pending)
    profile = ModelProfile(DRAFT["base_url"], FAKE_KEY, "chat", "coding")
    result = asyncio.run(run_connection_test(Store(), app.state.settings, profile, "chat", 1,
                                            "disconnect-probe", disconnected))
    assert result.ok is False
    assert result.error_code == "REQUEST_CANCELLED"
    assert finishes[0][0][1] == "cancelled"
