from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import time

from fastapi.testclient import TestClient
import pytest

from app.auth.routes import COOKIE_NAME
from app.auth.security import hash_password, verify_password
from app.auth.store import AuthStore, UsernameTaken
from app.main import create_app
from tests.fakes import FakeMailer, register_user


CREDS = {"username": "xiaoming", "password": "Example-password-2026!"}


def test_register_session_and_hash_at_rest(client, app):
    response = register_user(client, CREDS)
    assert response.status_code == 201
    assert response.json()["user"]["username"] == "xiaoming"
    assert "password" not in response.text
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie and "Path=/api" in cookie
    assert response.headers["cache-control"] == "no-store"
    assert client.get("/api/auth/me").json() == response.json()
    store = app.state.auth_store
    row = store.find_user("xiaoming")
    assert row["password_hash"].startswith("pbkdf2_sha256$600000$")
    assert CREDS["password"] not in row["password_hash"]
    with store.connect() as db:
        digest = db.execute("SELECT token_hash FROM sessions").fetchone()[0]
        assert digest != client.cookies.get(COOKIE_NAME)


def test_duplicate_username_is_case_insensitive(client):
    assert register_user(client, CREDS).status_code == 201
    assert register_user(client, {**CREDS, "username": "XIAOMING"}).status_code == 409


def test_login_errors_rotation_and_logout(client, app):
    register_user(client, CREDS)
    old_token = client.cookies.get(COOKIE_NAME)
    bad = client.post("/api/auth/login", json={**CREDS, "password": "Wrong-password-123"})
    missing = client.post("/api/auth/login", json={**CREDS, "username": "missing"})
    assert bad.status_code == missing.status_code == 401
    assert bad.json() == missing.json()
    login = client.post("/api/auth/login", json={**CREDS, "username": "XIAOMING"})
    assert login.status_code == 200
    new_token = client.cookies.get(COOKIE_NAME)
    assert new_token != old_token
    assert app.state.auth_store.session_user(old_token) is None
    assert client.post("/api/auth/logout").status_code == 204
    assert app.state.auth_store.session_user(new_token) is None
    assert client.get("/api/auth/me").status_code == 401
    assert client.post("/api/auth/logout").status_code == 204


def test_sessions_survive_restart_and_expire(client, app):
    register_user(client, CREDS)
    token = client.cookies.get(COOKIE_NAME)
    restarted = create_app(app.state.settings)
    with TestClient(restarted) as other:
        other.cookies.set(COOKIE_NAME, token, path="/api")
        assert other.get("/api/auth/me").status_code == 200
    with app.state.auth_store.connect() as db:
        db.execute("UPDATE sessions SET expires_at = ?", (int(time.time()) - 1,))
    assert client.get("/api/auth/me").status_code == 401
    assert client.post("/api/chat", json={"messages": [{"role": "user", "content": "你好"}]}).status_code == 401


def test_users_have_distinct_sessions(client, app):
    register_user(client, CREDS)
    with TestClient(app) as other:
        register_user(other, {**CREDS, "username": "other_user"})
        assert other.get("/api/auth/me").json()["user"]["username"] == "other_user"
        other.post("/api/auth/logout")
        assert client.get("/api/auth/me").json()["user"]["username"] == "xiaoming"


@pytest.mark.parametrize("changes", [{"username": "ab"}, {"username": "a b"}, {"password": "p@9"}, {"password": "x" * 129}, {"password": " " * 12}])
def test_invalid_credentials_do_not_echo_password(client, changes):
    data = {**CREDS, **changes}
    response = client.post("/api/auth/register", json=data)
    assert response.status_code == 422
    assert data["password"] not in response.text


def test_origin_checks_cover_login_logout_and_chat(client):
    headers = {"Origin": "https://untrusted.example"}
    assert client.post("/api/auth/register", json=CREDS, headers=headers).status_code == 403
    assert client.post("/api/auth/login", json=CREDS, headers=headers).status_code == 403
    assert register_user(client, CREDS, headers={"Origin": "http://127.0.0.1:5173"}).status_code == 201
    assert client.post("/api/auth/logout", headers=headers).status_code == 403
    assert client.post("/api/chat", json={"messages": [{"role": "user", "content": "hi"}]}, headers=headers).status_code == 403
    assert client.get("/api/auth/me").status_code == 200


def test_login_rate_limit(client, app):
    # 预置窗口计数，再验证一次真实失败与限流，避免重复计算密码哈希。
    with app.state.auth_store.connect() as db:
        db.execute("INSERT INTO auth_attempts VALUES (?, ?, ?)", ("user:xiaoming", 9, int(time.time()) + 300))
    assert client.post("/api/auth/login", json=CREDS).status_code == 401
    limited = client.post("/api/auth/login", json=CREDS)
    assert limited.status_code == 429
    assert int(limited.headers["retry-after"]) > 0
    with app.state.auth_store.connect() as db:
        db.execute("UPDATE auth_attempts SET reset_at = 0")
    assert client.post("/api/auth/login", json=CREDS).status_code == 401


def test_password_salts_and_verification():
    first, second = hash_password(CREDS["password"]), hash_password(CREDS["password"])
    assert first != second
    assert verify_password(CREDS["password"], first)
    assert not verify_password("different", first)
    assert not verify_password("different", "invalid")


def test_concurrent_registration_only_creates_one_user(tmp_path):
    store = AuthStore(tmp_path / "concurrent.sqlite3")
    store.initialize()
    encoded = hash_password(CREDS["password"])
    def create():
        try:
            store.create_user("same_user", encoded)
            return True
        except UsernameTaken:
            return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(lambda _: create(), range(2))).count(True) == 1


def test_https_cookie_setting(app):
    secure = create_app(replace(app.state.settings, session_cookie_secure=True))
    secure.state.mailer = FakeMailer()
    with TestClient(secure, base_url="https://testserver") as client:
        response = register_user(client, CREDS)
        assert "Secure" in response.headers["set-cookie"]
        assert client.get("/api/auth/me").status_code == 200


def test_schema_can_be_run_twice_without_losing_accounts(client, app):
    response = register_user(client, CREDS)
    app.state.auth_store.initialize()
    app.state.auth_store.initialize()
    assert client.get("/api/auth/me").json() == response.json()
    with app.state.auth_store.connect() as db:
        assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM users").fetchone()[0] == 1
