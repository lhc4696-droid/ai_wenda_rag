"""Source-aware accounting against real SQLite; no provider network."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import sqlite3
import time
from pathlib import Path

import pytest

from app.auth.store import AuthStore
from app.chat.store import ChatLimit, ModelCallStore
from app.migrations import initialize_database


@pytest.fixture
def meter(app, tmp_path):
    auth = AuthStore(tmp_path / "meter.sqlite3")
    auth.initialize()
    with auth.connect() as db:
        owner = db.execute("INSERT INTO users(username,password_hash,created_at) VALUES ('meter','unused',?)",
                           (int(time.time()),)).lastrowid
    return ModelCallStore(auth), owner, app.state.settings


def accept(store, owner, settings, identifier, source):
    store.reserve(identifier, owner, 1, settings, "same-model", source=source)
    store.finish(identifier, "succeeded", 1, input_tokens=10, output_tokens=5)


def test_custom_calls_do_not_consume_default_user_or_global_daily_allowance(meter):
    store, owner, settings = meter
    settings = replace(settings, chat=replace(settings.chat, requests_per_day=1, global_requests_per_day=1))
    accept(store, owner, settings, "custom-before", "custom")
    accept(store, owner, settings, "default", "default")
    accept(store, owner, settings, "custom-after", "custom")
    with pytest.raises(ChatLimit) as caught:
        accept(store, owner, settings, "default-exhausted", "default")
    assert caught.value.code == "CHAT_DAILY_LIMIT"
    usage = store.usage(owner, settings)
    assert usage["requests"] == 1 and usage["input_tokens"] == 10
    assert usage["custom"]["requests"] == 2 and usage["custom"]["input_tokens"] == 20
    assert usage["custom"]["daily_limit"] is None


def test_custom_calls_still_share_frequency_and_concurrency_protection(meter):
    store, owner, settings = meter
    store.reserve("running-default", owner, 1, settings)
    with pytest.raises(ChatLimit) as caught:
        store.reserve("running-custom", owner, 1, settings, source="custom")
    assert caught.value.code == "CHAT_USER_BUSY"
    store.finish("running-default", "failed", 1)
    settings = replace(settings, chat=replace(settings.chat, requests_per_minute=1))
    with pytest.raises(ChatLimit) as caught:
        store.reserve("frequent-custom", owner, 1, settings, source="custom")
    assert caught.value.code == "CHAT_RATE_LIMIT"


def test_default_daily_reservation_remains_atomic_across_sources(meter):
    store, owner, settings = meter
    settings = replace(settings, chat=replace(settings.chat, requests_per_day=1, user_concurrency=4))
    accept(store, owner, settings, "custom", "custom")
    def reserve(identifier):
        try:
            store.reserve(identifier, owner, 1, settings, source="default")
            return "accepted"
        except ChatLimit as exc:
            return exc.code
    with ThreadPoolExecutor(max_workers=2) as workers:
        outcomes = list(workers.map(reserve, ["one", "two"]))
    assert sorted(outcomes) == ["CHAT_DAILY_LIMIT", "accepted"]


def test_embedding_budget_is_separate_and_checks_before_reserving(meter):
    store, owner, settings = meter
    settings = replace(settings, rag=replace(settings.rag, requests_per_day=1, global_requests_per_day=1))
    # An embedding sub-call can run inside its parent chat reservation.
    store.reserve("chat", owner, 1, settings, source="custom")
    accept(store, owner, settings, "embedding", "embedding")
    with pytest.raises(ChatLimit) as caught:
        store.reserve("extra-embedding", owner, 1, settings, source="embedding")
    assert caught.value.code == "RAG_DAILY_LIMIT"
    assert store.usage(owner, settings)["embedding"]["requests"] == 1
    assert store.usage(owner, settings)["requests"] == 0


def test_server_daily_budgets_span_users_but_do_not_block_personal_calls(meter):
    store, owner, settings = meter
    settings = replace(settings, chat=replace(settings.chat, global_requests_per_day=1),
                       rag=replace(settings.rag, global_requests_per_day=1))
    with store.auth.connect() as db:
        other = db.execute("INSERT INTO users(username,password_hash,created_at) VALUES ('other','unused',1)").lastrowid
    accept(store, owner, settings, 'server-default', 'default')
    accept(store, owner, settings, 'server-embedding', 'embedding')
    for source, code in [('default', 'CHAT_GLOBAL_DAILY_LIMIT'), ('embedding', 'RAG_GLOBAL_DAILY_LIMIT')]:
        with pytest.raises(ChatLimit) as caught:
            store.reserve('blocked-' + source, other, 1, settings, source=source)
        assert caught.value.code == code
    accept(store, other, settings, 'personal', 'custom')
    assert store.usage(other, settings)['requests'] == 0
    assert store.usage(other, settings)['custom']['requests'] == 1


def test_failed_custom_call_is_counted_without_reducing_default_allowance(meter):
    store, owner, settings = meter
    settings = replace(settings, chat=replace(settings.chat, requests_per_day=1))
    store.reserve('failed-personal', owner, 1, settings, source='custom')
    store.finish('failed-personal', 'failed', 1, error_code='LLM_AUTH_FAILED')
    accept(store, owner, settings, 'default-still-available', 'default')
    usage = store.usage(owner, settings)
    assert usage['requests'] == usage['succeeded'] == 1
    assert usage['custom']['requests'] == 1 and usage['custom']['succeeded'] == 0


def test_legacy_migration_preserves_unknown_source_and_is_repeatable(tmp_path, app):
    path = tmp_path / "v12.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE users (id INTEGER PRIMARY KEY,username TEXT UNIQUE,password_hash TEXT,created_at INTEGER,email TEXT,email_verified_at INTEGER,auth_version INTEGER DEFAULT 0)")
        db.execute("INSERT INTO users VALUES (1,'old','unused',1,NULL,NULL,0)")
        db.executescript((Path(__file__).parents[1] / 'sql/migrations/002_model_calls.sql').read_text(encoding='utf-8'))
        db.execute("INSERT INTO model_calls(request_id,user_id,model,created_at,lease_expires_at,state,input_chars) VALUES ('old',1,'same-model',?,0,'succeeded',1)", (int(time.time()),))
    initialize_database(path)
    initialize_database(path)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT model,source FROM model_calls WHERE request_id='old'").fetchone() == ("same-model", "legacy")
        assert db.execute("SELECT max(version) FROM schema_migrations").fetchone()[0] == 13
    usage = ModelCallStore(AuthStore(path)).usage(1, app.state.settings)
    assert usage['requests'] == usage['legacy_requests'] == 1
    assert usage['custom']['requests'] == 0

