"""Username rules shared by registration, login and account rename."""

import pytest
from pydantic import ValidationError

from app.auth.schemas import Credentials, RegisterRequest, RenameAccountRequest
from app.auth.store import AuthStore, TooManyAttempts
from tests.fakes import register_user


MODELS = [Credentials, RegisterRequest, RenameAccountRequest]


def validate(model, username):
    values = {"username": username}
    if model is not RenameAccountRequest:
        values["password"] = "Example-password-2026!"
    if model is RegisterRequest:
        values.update(email="tester@example.com", code="123456")
    return model(**values).username


@pytest.mark.parametrize("model", MODELS)
@pytest.mark.parametrize("value,expected", [
    ("唯", "唯"), ("a", "a"), ("ab", "ab"), ("平泽唯", "平泽唯"),
    ("Alice·陈-01", "Alice·陈-01"), ("小 明", "小 明"),
    ("Élodie", "Élodie"), ("山田さん", "山田さん"), ("مرحبا", "مرحبا"),
    ("🙂", "🙂"), ("👩‍💻", "👩‍💻"), ("❤\ufe0f", "❤\ufe0f"),
    ("e\u0301", "e\u0301"), ("<小明>&'", "<小明>&'"),
    (" \u3000平泽唯 \u3000", "平泽唯"), (" new_name ", "new_name"),
    ("中" * 32, "中" * 32), ("🙂" * 32, "🙂" * 32),
], ids=lambda v: ascii(v)[:60])
def test_accepts_visible_unicode_and_one_character(model, value, expected):
    assert validate(model, value) == expected


@pytest.mark.parametrize("model", MODELS)
@pytest.mark.parametrize("value", [
    "", " \u3000 ", "中" * 33, "🙂" * 33, "\n小明", "小明\n", "小\t明",
    "小\x00明", "小\x7f明", "小\u0085明", "小\u2028明", "小\u2029明",
    "小\u202e明", "小\u2066明", "小\u200b明", "\ufeff小明", "\ud800",
    "\u200d", "\u0301", "\ufe0f", "\u200d小明", "小明\u200d",
    "\u115f", "\u1160", "\u3164", "\uffa0", "\u2800",
], ids=lambda v: ascii(v)[:60])
def test_rejects_blank_oversize_and_hidden_control_characters(model, value):
    with pytest.raises(ValidationError):
        validate(model, value)


def test_register_login_rename_and_duplicate_unicode_username(client, app):
    password = "Example-password-2026!"
    registered = register_user(client, {"username": " 唯 ", "password": password})
    assert registered.status_code == 201, registered.text
    user_id = registered.json()["user"]["id"]
    assert registered.json()["user"]["username"] == "唯"
    client.post("/api/auth/logout")
    assert client.post("/api/auth/login", json={"username": "唯", "password": password}).status_code == 200
    renamed = client.post("/api/auth/username", json={"username": " 小明🙂·01 "})
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["user"]["id"] == user_id
    assert client.get("/api/auth/me").json()["user"]["username"] == "小明🙂·01"
    assert app.state.auth_store.find_user("唯") is None
    assert client.post("/api/auth/login", json={"username": "小明🙂·01", "password": password}).status_code == 200
    duplicate = register_user(client, {"username": "小明🙂·01", "password": password})
    assert duplicate.status_code == 409


def test_login_rate_limit_matches_sqlite_username_identity(tmp_path):
    store = AuthStore(tmp_path / "unicode-rate-limit.sqlite3")
    store.initialize()
    first = store.create_user("Élodie", "unused")
    second = store.create_user("élodie", "unused")
    assert first.id != second.id
    for _ in range(10):
        store.reserve_auth_attempt("login", "first-ip", "Élodie")
    with pytest.raises(TooManyAttempts):
        store.reserve_auth_attempt("login", "second-ip", "ÉLODIE")
    store.reserve_auth_attempt("login", "second-ip", "élodie")
