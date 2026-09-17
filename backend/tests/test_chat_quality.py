import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import time

import pytest

from app.chat.service import bounded_context
from app.chat.store import ChatLimit
from app.main import get_llm_provider
from app.schemas import ChatMessage
from app.services.llm import LLMError, LLMResult
from tests.fakes import register_user


QUESTION = {"messages": [{"role": "user", "content": "你好"}]}


def test_long_answer_is_bounded_and_follow_up_remains_valid(logged_in, app):
    class LongModel:
        async def chat(self, messages):
            return LLMResult("答" * 25_000, 12, 30_000, "length")
    app.dependency_overrides[get_llm_provider] = LongModel
    first = logged_in.post("/api/chat", json=QUESTION)
    assert first.status_code == 200
    data = first.json()
    assert len(data["content"]) == 20_000 and data["answer_truncated"]
    assert data["request_id"] == first.headers["X-Request-ID"]
    follow_up = {"messages": [*QUESTION["messages"], {"role": "assistant", "content": data["content"]}, {"role": "user", "content": "继续"}]}
    assert logged_in.post("/api/chat", json=follow_up).status_code == 200
    stats = logged_in.get("/api/chat/usage").json()
    assert stats["requests"] == 2 and stats["input_tokens"] == 24
    with app.state.auth_store.connect() as db:
        columns = {row[1] for row in db.execute("PRAGMA table_info(model_calls)")}
        assert "content" not in columns and "messages" not in columns


def test_context_budget_preserves_recent_complete_turns(app):
    settings = replace(app.state.settings, chat_context_chars=20_000)
    messages = [ChatMessage(role="user", content="旧" * 10_000), ChatMessage(role="assistant", content="答" * 10_000), ChatMessage(role="user", content="新问题")]
    selected, trimmed = bounded_context(messages, settings)
    assert trimmed and selected == [messages[-1]]
    assert len(messages) == 3


def test_limits_are_atomic_and_count_failed_accepted_calls(logged_in, app):
    store, settings = app.state.model_calls, app.state.settings
    user_id = logged_in.get("/api/auth/me").json()["user"]["id"]
    def reserve(identifier):
        try:
            store.reserve(identifier, user_id, 2, settings)
            return True
        except ChatLimit:
            return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(reserve, ["parallel-a", "parallel-b"])).count(True) == 1
    with app.state.auth_store.connect() as db:
        identifier = db.execute("SELECT request_id FROM model_calls WHERE state = 'running'").fetchone()[0]
    store.finish(identifier, "failed", 1, error_code="LLM_UNAVAILABLE")
    with pytest.raises(ChatLimit) as exc:
        store.reserve("minute-limit", user_id, 2, replace(settings, chat_requests_per_minute=1))
    assert exc.value.code == "CHAT_RATE_LIMIT"
    with pytest.raises(ChatLimit) as exc:
        store.reserve("daily-limit", user_id, 2, replace(settings, chat_requests_per_day=1))
    assert exc.value.code == "CHAT_DAILY_LIMIT"


def test_global_concurrency_and_abandoned_lease_recovery(logged_in, app):
    store, settings = app.state.model_calls, replace(app.state.settings, chat_global_concurrency=1)
    user_id = logged_in.get("/api/auth/me").json()["user"]["id"]
    store.reserve("first", user_id, 2, settings)
    with pytest.raises(ChatLimit) as exc:
        store.reserve("other", user_id + 1, 2, settings)
    assert exc.value.code == "CHAT_SERVER_BUSY"
    with app.state.auth_store.connect() as db:
        db.execute("UPDATE model_calls SET lease_expires_at = 0")
    store.reserve("after-expiry", user_id, 2, settings)
    with app.state.auth_store.connect() as db:
        assert db.execute("SELECT state FROM model_calls WHERE request_id = 'first'").fetchone()[0] == "abandoned"


def test_timeout_and_rate_limit_have_safe_codes_and_release_slots(logged_in, app):
    class TimedOut:
        async def chat(self, messages):
            raise asyncio.TimeoutError
    app.dependency_overrides[get_llm_provider] = TimedOut
    response = logged_in.post("/api/chat", json=QUESTION)
    assert response.status_code == 504 and response.headers["X-Error-Code"] == "LLM_TIMEOUT"
    class Limited:
        async def chat(self, messages):
            raise LLMError("模型繁忙", "LLM_RATE_LIMIT", 429, 30)
    app.dependency_overrides[get_llm_provider] = Limited
    response = logged_in.post("/api/chat", json=QUESTION)
    assert response.status_code == 429 and response.headers["Retry-After"] == "30"
    with app.state.auth_store.connect() as db:
        assert db.execute("SELECT count(*) FROM model_calls WHERE state = 'running'").fetchone()[0] == 0


def test_usage_cannot_read_another_accounts_calls(client, app):
    owner = register_user(client, {"username": "owner", "password": "abcd1234"}).json()["user"]
    app.state.model_calls.reserve("owner-call", owner["id"], 1, app.state.settings)
    client.post("/api/auth/logout")
    assert client.get("/api/chat/usage").status_code == 401
    register_user(client, {"username": "visitor", "password": "abcd1234"})
    assert client.get(f"/api/chat/usage?user_id={owner['id']}").json()["requests"] == 0
