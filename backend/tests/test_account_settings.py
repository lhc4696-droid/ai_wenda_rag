from fastapi.testclient import TestClient
import pytest
import sqlite3

from app.auth.routes import COOKIE_NAME
from app.auth.security import hash_password
from app.auth.store import SessionChanged
from tests.fakes import register_user, request_code


CREDS = {"username": "account_user", "password": "abcd1234"}


def test_rename_keeps_session_without_password_confirmation(client, app):
    user = register_user(client, CREDS).json()["user"]
    token = client.cookies.get(COOKIE_NAME)
    invalid = client.post("/api/auth/username", json={"username": "ab"})
    assert invalid.status_code == 422
    assert app.state.auth_store.find_user("account_user") is not None
    renamed = client.post("/api/auth/username", json={"username": "new_name"})
    assert renamed.status_code == 200
    assert renamed.json()["user"] == {**user, "username": "new_name"}
    assert app.state.auth_store.session_user(token).username == "new_name"
    assert client.post("/api/auth/login", json=CREDS).status_code == 401
    assert client.post("/api/auth/login", json={**CREDS, "username": "new_name"}).status_code == 200


def test_duplicate_username_and_user_id_injection_are_rejected(client, app):
    register_user(client, CREDS)
    app.state.auth_store.create_user("taken", hash_password("abcdefgh"))
    assert client.post("/api/auth/username", json={"username": "TAKEN"}).status_code == 409
    response = client.post("/api/auth/username", json={"username": "changed", "user_id": 2})
    assert response.status_code == 422
    assert CREDS["password"] not in response.text


def test_change_password_rotates_current_session_and_revokes_other_sessions(client, app):
    user = register_user(client, CREDS).json()["user"]
    old_token = client.cookies.get(COOKIE_NAME)
    request_code(client, user["email"], "reset_password")
    with TestClient(app) as other:
        assert other.post("/api/auth/login", json=CREDS).status_code == 200
        changed = client.post("/api/auth/password/change", json={"current_password": CREDS["password"], "new_password": "newpass8"})
        assert changed.status_code == 200
        new_token = client.cookies.get(COOKIE_NAME)
        assert new_token and new_token != old_token
        assert app.state.auth_store.session_user(old_token) is None
        assert client.get("/api/auth/me").json()["user"] == user
        assert other.get("/api/auth/me").status_code == 401
        assert new_token not in changed.text
        assert changed.headers["Cache-Control"] == "no-store"
        cookie = changed.headers["set-cookie"].lower()
        assert "httponly" in cookie and "samesite=lax" in cookie and "path=/api" in cookie
    with app.state.auth_store.connect() as db:
        assert db.execute("SELECT count(*) FROM email_verifications WHERE status IN ('pending', 'active')").fetchone()[0] == 0
    assert client.post("/api/auth/login", json=CREDS).status_code == 401
    assert client.post("/api/auth/login", json={**CREDS, "password": "newpass8"}).status_code == 200
    assert app.state.mailer.notifications == [user["email"]]


def test_new_session_failure_rolls_back_password_and_revocation(client, app):
    user = register_user(client, CREDS).json()["user"]
    store = app.state.auth_store
    before = store.find_user_by_id(user["id"])
    token = client.cookies.get(COOKIE_NAME)
    # 仅此用例的临时数据库注入写入失败，验证整个改密事务回滚。
    with store.connect() as db:
        db.execute("CREATE TRIGGER reject_replacement BEFORE INSERT ON sessions BEGIN SELECT RAISE(ABORT, 'test replacement failure'); END")
    with pytest.raises(sqlite3.IntegrityError):
        store.change_account_password(user["id"], before["auth_version"], token, hash_password("newpass8"), 3600)
    after = store.find_user_by_id(user["id"])
    assert after["password_hash"] == before["password_hash"]
    assert after["auth_version"] == before["auth_version"]
    assert store.session_user(token).id == user["id"]


def test_revoked_session_cannot_change_password_or_create_replacement(client, app):
    user = register_user(client, CREDS).json()["user"]
    store = app.state.auth_store
    before = store.find_user_by_id(user["id"])
    token = client.cookies.get(COOKIE_NAME)
    store.revoke_session(token)
    with pytest.raises(SessionChanged):
        store.change_account_password(user["id"], before["auth_version"], token, hash_password("newpass8"), 3600)
    assert store.find_user_by_id(user["id"])["password_hash"] == before["password_hash"]
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM sessions WHERE user_id = ?", (user["id"],)).fetchone()[0] == 0


def test_legacy_account_can_change_password_without_email(client, app):
    app.state.auth_store.create_user(CREDS["username"], hash_password(CREDS["password"]))
    assert client.post("/api/auth/login", json=CREDS).status_code == 200
    app.state.mailer.fail = True
    assert client.post("/api/auth/password/change", json={"current_password": CREDS["password"], "new_password": "newpass8"}).status_code == 200


@pytest.mark.parametrize("path,payload", [
    ("username", {"username": "new_name"}),
    ("password/change", {"current_password": "abcd1234", "new_password": "newpass8"}),
])
def test_settings_endpoints_require_login_and_trusted_origin(client, path, payload):
    assert client.post(f"/api/auth/{path}", json=payload).status_code == 401
    register_user(client, CREDS)
    assert client.post(f"/api/auth/{path}", json=payload, headers={"Origin": "https://untrusted.invalid"}).status_code == 403


def test_sensitive_operations_are_rate_limited(client):
    register_user(client, CREDS)
    for index in range(5):
        assert client.post("/api/auth/username", json={"username": f"new_name_{index}"}).status_code == 200
    blocked = client.post("/api/auth/password/change", json={"current_password": CREDS["password"], "new_password": "newpass8"})
    assert blocked.status_code == 429 and int(blocked.headers["Retry-After"]) > 0


def test_account_writes_recheck_version_and_session(client, app):
    user = register_user(client, CREDS).json()["user"]
    old = app.state.auth_store.find_user_by_id(user["id"])
    token = client.cookies.get(COOKIE_NAME)
    client.post("/api/auth/password/change", json={"current_password": CREDS["password"], "new_password": "newpass8"})
    with pytest.raises(SessionChanged):
        app.state.auth_store.rename_account(user["id"], old["auth_version"], token, "stale_name")


def test_same_password_and_short_password_are_rejected(client):
    register_user(client, CREDS)
    assert client.post("/api/auth/password/change", json={"current_password": "wrongpwd", "new_password": "newpass8"}).status_code == 400
    assert client.post("/api/auth/password/change", json={"current_password": CREDS["password"], "new_password": CREDS["password"]}).status_code == 400
    invalid = client.post("/api/auth/password/change", json={"current_password": CREDS["password"], "new_password": "abc1234"})
    assert invalid.status_code == 422 and "abc1234" not in invalid.text
