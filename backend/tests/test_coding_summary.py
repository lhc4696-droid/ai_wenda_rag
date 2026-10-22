"""Legacy footer compatibility without opening a database or calling a model."""

from contextlib import nullcontext
from copy import deepcopy
from types import SimpleNamespace

from app.coding.local_store import (
    LEGACY_SYSTEM_NOTICE, LocalCodingStore, clean_summary, historical_messages, public_local,
)
from app.coding.model import CodingReply


def test_repeated_legacy_footers_are_removed():
    assert clean_summary("已完成。\n\n" + (LEGACY_SYSTEM_NOTICE + "\n\n") * 3) == "已完成。"
    assert clean_summary(LEGACY_SYSTEM_NOTICE) == ""
    assert clean_summary("") == ""
    assert clean_summary(None) is None


def test_specific_warnings_and_quoted_examples_are_preserved():
    text = "尚未在浏览器验证 SVG。\n\n```text\n" + LEGACY_SYSTEM_NOTICE + "\n```"
    assert clean_summary(text) == text
    assert clean_summary(text + "\n\n" + LEGACY_SYSTEM_NOTICE) == text
    assert clean_summary("解释这句话：" + LEGACY_SYSTEM_NOTICE) == "解释这句话：" + LEGACY_SYSTEM_NOTICE


def test_history_removes_footer_without_modifying_rows():
    rows = [{"instruction": "修改图形", "summary": "已修改，未验证。\n\n" + LEGACY_SYSTEM_NOTICE,
             "error": "", "status": "completed"}]
    original = deepcopy(rows)
    messages = historical_messages(rows)
    assert "已修改，未验证。" in messages[1]["content"]
    assert LEGACY_SYSTEM_NOTICE not in messages[1]["content"]
    assert rows == original


def test_public_response_cleans_legacy_summary_without_mutating_task():
    task = dict.fromkeys(("id", "conversation_id", "turn_index", "client_task_id", "directory_name",
                         "instruction", "status", "round", "pending_tools", "steps", "summary", "error",
                         "created_at", "updated_at", "deadline_at"))
    task["summary"] = "完成\n\n" + (LEGACY_SYSTEM_NOTICE + "\n\n") * 2
    original = deepcopy(task)
    assert public_local(task)["summary"] == "完成"
    assert task == original


def test_completed_reply_does_not_append_fixed_notice(monkeypatch):
    db = SimpleNamespace(execute=lambda *args: None)
    store = LocalCodingStore(SimpleNamespace(connect=lambda: nullcontext(db)))
    task = {"status": "running", "messages": [], "steps": []}
    saved = {}
    monkeypatch.setattr(store, "_expire", lambda db: None)
    monkeypatch.setattr(store, "_owned", lambda *args: task)
    monkeypatch.setattr(store, "_stop", lambda db, task, status, **kwargs: saved.update(kwargs))
    store.reply(1, "test", CodingReply("已修改。\n\n" + LEGACY_SYSTEM_NOTICE, []))
    assert saved["summary"] == "已修改。"
    store.reply(1, "test", CodingReply("尚未验证效果。", []))
    assert saved["summary"] == "尚未验证效果。"
