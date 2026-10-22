from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import sqlite3
import time

import pytest
from fastapi.testclient import TestClient

from app.auth.routes import COOKIE_NAME
from app.auth.schemas import normalize_email
from app.auth.security import hash_password, verify_password
from app.auth.store import AuthStore, InvalidEmailCode, SessionChanged
from app.auth.verification import EmailVerification
from app.main import create_app
from app.integrations.email import SMTPMailer
from tests.fakes import FakeMailer, register_user, request_code


CREDS = {"username": "email_user", "password": "Original-password-2026!"}
EMAIL = "owner@example.com"
NEW_PASSWORD = "Replacement-password-2026!"


def enroll(client):
    code = request_code(client, EMAIL)
    response = client.post("/api/auth/register", json={**CREDS, "email": EMAIL, "code": code})
    assert response.status_code == 201, response.text
    return code


def reset(client, code, email=EMAIL):
    return client.post("/api/auth/password/reset", json={"email": email, "code": code, "new_password": NEW_PASSWORD})


def test_verified_register_and_secret_storage(client, app):
    code = enroll(client)
    row = app.state.auth_store.find_user(CREDS["username"])
    assert row["email"] == EMAIL and row["email_verified_at"] is not None
    assert client.get("/api/auth/me").json()["user"]["email"] == EMAIL
    with app.state.auth_store.connect() as db:
        record = db.execute("SELECT * FROM email_verifications").fetchone()
        assert record["status"] == "used" and len(record["code_hash"]) == 64
        assert record["code_hash"] != code
    replay = client.post("/api/auth/register", json={**CREDS, "username": "different_user", "email": EMAIL, "code": code})
    assert replay.status_code == 400


def test_registration_cannot_skip_email_verification(client):
    assert client.post("/api/auth/register", json=CREDS).status_code == 422
    assert client.post("/api/auth/register", json={**CREDS, "email": EMAIL, "code": "123456"}).status_code == 400


def test_password_reset_revokes_all_sessions_and_requires_new_login(client, app):
    enroll(client)
    first = client.cookies.get(COOKIE_NAME)
    with TestClient(app) as other:
        assert other.post("/api/auth/login", json=CREDS).status_code == 200
        second = other.cookies.get(COOKIE_NAME)
        code = request_code(client, EMAIL, "reset_password")
        response = reset(client, code)
        assert response.status_code == 200
        assert NEW_PASSWORD not in response.text
        assert app.state.auth_store.session_user(first) is None
        assert app.state.auth_store.session_user(second) is None
        assert other.get("/api/auth/me").status_code == 401
    assert client.get("/api/auth/me").status_code == 401
    assert client.post("/api/auth/login", json=CREDS).status_code == 401
    assert client.post("/api/auth/login", json={**CREDS, "password": NEW_PASSWORD}).status_code == 200
    assert reset(client, code).status_code == 400
    assert app.state.mailer.notifications == [EMAIL]


def test_unknown_reset_and_existing_reset_have_same_response(client, app):
    enroll(client)
    known = client.post("/api/auth/email-code", json={"email": EMAIL, "purpose": "reset_password"})
    before = len(app.state.mailer.codes)
    unknown = client.post("/api/auth/email-code", json={"email": "unknown@example.com", "purpose": "reset_password"})
    assert known.status_code == unknown.status_code == 202
    assert known.json() == unknown.json()
    assert len(app.state.mailer.codes) == before
    assert reset(client, "123456", "unknown@example.com").status_code == 400
    assert client.post("/api/auth/login", json=CREDS).status_code == 200


def test_code_is_bound_to_email_and_purpose(client):
    code = request_code(client, EMAIL)
    assert reset(client, code).status_code == 400
    assert client.post("/api/auth/register", json={**CREDS, "email": "other@example.com", "code": code}).status_code == 400
    assert client.post("/api/auth/register", json={**CREDS, "email": EMAIL, "code": code}).status_code == 201


def test_expiry_and_resend(client, app):
    old = request_code(client, EMAIL)
    with app.state.auth_store.connect() as db:
        db.execute("UPDATE email_verifications SET expires_at = 0")
    assert client.post("/api/auth/register", json={**CREDS, "email": EMAIL, "code": old}).status_code == 400
    limited = client.post("/api/auth/email-code", json={"email": EMAIL, "purpose": "register"})
    assert limited.status_code == 429 and int(limited.headers["retry-after"]) > 0
    with app.state.auth_store.connect() as db:
        db.execute("UPDATE auth_attempts SET reset_at = 0")
    new = request_code(client, EMAIL)
    assert client.post("/api/auth/register", json={**CREDS, "email": EMAIL, "code": new}).status_code == 201


def test_wrong_attempts_commit_and_exhaust_code(client, app):
    correct = request_code(client, EMAIL)
    wrong = "000000" if correct != "000000" else "111111"
    for _ in range(5):
        assert client.post("/api/auth/register", json={**CREDS, "email": EMAIL, "code": wrong}).status_code == 400
    with app.state.auth_store.connect() as db:
        row = db.execute("SELECT attempts, status FROM email_verifications").fetchone()
        assert tuple(row) == (5, "failed")
    assert client.post("/api/auth/register", json={**CREDS, "email": EMAIL, "code": correct}).status_code == 400


def test_background_failure_does_not_leak_or_activate(client, app, caplog):
    app.state.mailer.fail = True
    response = client.post("/api/auth/email-code", json={"email": EMAIL, "purpose": "register"})
    assert response.status_code == 202
    assert "code" not in response.json()
    with app.state.auth_store.connect() as db:
        assert db.execute("SELECT status FROM email_verifications").fetchone()[0] == "failed"
    assert EMAIL not in caplog.text
    assert client.post("/api/auth/register", json={**CREDS, "email": EMAIL, "code": "123456"}).status_code == 400


def test_missing_configuration_is_clear_without_breaking_legacy_login(app):
    settings = replace(app.state.settings, email_code=replace(app.state.settings.email_code, secret=""), smtp=replace(app.state.settings.smtp, host="", password=""))
    application = create_app(settings)
    with TestClient(application) as client:
        response = client.post("/api/auth/email-code", json={"email": EMAIL, "purpose": "register"})
        assert response.status_code == 503 and "EMAIL_CODE_SECRET" in response.text
        application.state.auth_store.create_user("legacy", hash_password(CREDS["password"]))
        assert client.post("/api/auth/login", json={**CREDS, "username": "legacy"}).status_code == 200


def test_old_schema_migration_preserves_accounts_and_sessions(tmp_path):
    path = tmp_path / "old.sqlite3"
    with sqlite3.connect(path) as db:
        db.executescript("CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL UNIQUE COLLATE NOCASE, password_hash TEXT NOT NULL, created_at INTEGER NOT NULL);")
        db.execute("INSERT INTO users VALUES (7, 'legacy', ?, 123)", (hash_password(CREDS["password"]),))
    store = AuthStore(path)
    store.initialize()
    store.initialize()
    row = store.find_user("legacy")
    assert row["id"] == 7 and row["email"] is None and row["email_verified_at"] is None
    assert verify_password(CREDS["password"], row["password_hash"])
    with store.connect() as db:
        assert [row[0] for row in db.execute("SELECT version FROM schema_migrations ORDER BY version")] == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13]
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_concurrent_code_consumption_only_one_registration(client, app):
    code = request_code(client, EMAIL)
    password_hash = hash_password(CREDS["password"])
    def run(index):
        try:
            app.state.auth_store.apply_email_code(EMAIL, "register", code, password_hash, app.state.settings, f"person_{index}")
            return True
        except InvalidEmailCode:
            return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(run, range(2))).count(True) == 1


def test_reset_prevents_inflight_old_password_login(client, app):
    enroll(client)
    previous = app.state.auth_store.find_user(CREDS["username"])
    code = request_code(client, EMAIL, "reset_password")
    assert reset(client, code).status_code == 200
    with pytest.raises(SessionChanged):
        app.state.auth_store.create_session(previous["id"], 60, None, expected_version=previous["auth_version"])


def test_delayed_old_delivery_cannot_activate_superseded_code(client, app):
    service = EmailVerification(app.state.auth_store, app.state.settings, app.state.mailer)
    first_id, first_code, _ = service.prepare(EMAIL, "register", "test-ip")
    with app.state.auth_store.connect() as db:
        db.execute("UPDATE auth_attempts SET reset_at = 0")
    second_id, second_code, _ = service.prepare(EMAIL, "register", "test-ip")
    service.deliver(second_id, second_code, EMAIL, "register")
    service.deliver(first_id, first_code, EMAIL, "register")
    assert len(app.state.mailer.codes) == 1
    with app.state.auth_store.connect() as db:
        assert db.execute("SELECT status FROM email_verifications WHERE id = ?", (first_id,)).fetchone()[0] == "superseded"


@pytest.mark.parametrize("changes", [{"email": "not-an-email"}, {"code": "１２３４５６"}, {"code": "abcdef"}, {"new_password": " " * 12}, {"new_password": "x1!"}])
def test_reset_validation_never_echoes_secrets(client, changes):
    data = {"email": EMAIL, "code": "867321", "new_password": NEW_PASSWORD, **changes}
    response = client.post("/api/auth/password/reset", json=data)
    assert response.status_code == 422
    assert data["code"] not in response.text and data["new_password"] not in response.text


def test_email_normalization_and_documented_cookie_auth(client):
    assert normalize_email(" Owner@EXAMPLE.COM ") == "Owner@example.com"
    schema = client.get("/openapi.json").json()
    assert schema["components"]["securitySchemes"]["APIKeyCookie"]["in"] == "cookie"
    assert schema["paths"]["/api/chat"]["post"]["security"]
    for path in ("/api/auth/email-code", "/api/auth/password/reset"):
        assert path in schema["paths"]


def test_unknown_and_registered_email_cannot_bypass_unique_email(client, app):
    enroll(client)
    with app.state.auth_store.connect() as db:
        db.execute("UPDATE auth_attempts SET reset_at = 0")
    count = len(app.state.mailer.codes)
    response = client.post("/api/auth/email-code", json={"email": EMAIL, "purpose": "register"})
    assert response.status_code == 202 and len(app.state.mailer.codes) == count
    with app.state.auth_store.connect() as db:
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("INSERT INTO users (username, password_hash, created_at, email) VALUES ('duplicate', 'x', 1, ?)", (EMAIL,))


def test_new_email_endpoints_reject_untrusted_origin(client):
    headers = {"Origin": "https://untrusted.example"}
    assert client.post("/api/auth/email-code", json={"email": EMAIL, "purpose": "register"}, headers=headers).status_code == 403
    assert client.post("/api/auth/password/reset", json={"email": EMAIL, "code": "123456", "new_password": NEW_PASSWORD}, headers=headers).status_code == 403


@pytest.mark.parametrize("mode", ["ssl", "starttls"])
def test_smtp_uses_encryption_and_does_not_include_password(app, monkeypatch, mode):
    import app.integrations.email as email_module
    actions = []
    messages = []
    class SMTPConnection:
        def __init__(self, *args, **kwargs):
            actions.append("connect")
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def ehlo(self): actions.append("ehlo")
        def starttls(self, **kwargs): actions.append("tls")
        def login(self, username, password): actions.append("login")
        def send_message(self, message):
            messages.append(message)
            return {}
    monkeypatch.setattr(email_module.smtplib, "SMTP_SSL" if mode == "ssl" else "SMTP", SMTPConnection)
    settings = replace(app.state.settings, smtp=replace(app.state.settings.smtp, host="smtp.test.invalid", username="sender", password="test-smtp-secret", from_address="sender@example.com", security=mode))
    SMTPMailer(settings).send_code(EMAIL, "003821", "register", 300)
    assert messages[0]["To"] == EMAIL
    assert "003821" in messages[0].get_content()
    assert "test-smtp-secret" not in str(messages[0])
    if mode == "starttls":
        assert actions.index("tls") < actions.index("login")
