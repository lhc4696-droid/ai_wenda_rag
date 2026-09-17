from contextlib import contextmanager
from pathlib import Path
import secrets
import sqlite3
import time
import hmac

from app.auth.schemas import UserResponse
from app.auth.security import email_code_digest, generate_email_code, token_digest
from app.config import Settings
from app.migrations import initialize_database


class UsernameTaken(Exception):
    pass


class TooManyAttempts(Exception):
    def __init__(self, retry_after: int):
        self.retry_after = retry_after


class InvalidEmailCode(Exception):
    pass


class SessionChanged(Exception):
    pass


class AuthStore:
    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def initialize(self):
        initialize_database(self.path)

    @staticmethod
    def _reserve_buckets(db, buckets, now):
        db.execute("DELETE FROM auth_attempts WHERE reset_at <= ?", (now,))
        for bucket, limit, window in buckets:
            row = db.execute("SELECT count, reset_at FROM auth_attempts WHERE bucket = ?", (bucket,)).fetchone()
            if row and row["count"] >= limit:
                raise TooManyAttempts(max(1, row["reset_at"] - now))
        for bucket, limit, window in buckets:
            db.execute("INSERT INTO auth_attempts VALUES (?, 1, ?) ON CONFLICT(bucket) DO UPDATE SET count = count + 1", (bucket, now + window))

    def reserve_code_check(self, email: str, ip: str):
        now = int(time.time())
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._reserve_buckets(db, [(f"verify:ip:{ip}", 40, 300), (f"verify:email:{token_digest(email)}", 20, 300)], now)

    def prepare_email_code(self, email: str, purpose: str, ip: str, settings: Settings):
        """先占用配额和当前挑战；网络投递在事务结束后完成。"""
        now = int(time.time())
        record_id, code = secrets.token_urlsafe(24), generate_email_code()
        digest = email_code_digest(settings.email_code_secret, record_id, email, purpose, code)
        email_key = token_digest(email)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._reserve_buckets(db, [
                ("email:global", 100, 3600), (f"email:ip:{ip}", 20, 3600),
                (f"email:address:{email_key}", 5, 3600),
                (f"email:cooldown:{email_key}:{purpose}", 1, settings.email_code_cooldown),
            ], now)
            db.execute("DELETE FROM email_verifications WHERE expires_at < ?", (now - 86400,))
            user = db.execute("SELECT id, email_verified_at FROM users WHERE email = ?", (email,)).fetchone()
            user_id = user["id"] if user and user["email_verified_at"] is not None else None
            deliver = user is None if purpose == "register" else user_id is not None
            db.execute("UPDATE email_verifications SET status = 'superseded' WHERE email = ? AND purpose = ? AND status IN ('pending', 'active')", (email, purpose))
            db.execute(
                "INSERT INTO email_verifications (id, email, purpose, user_id, code_hash, created_at, expires_at, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (record_id, email, purpose, user_id, digest, now, now + settings.email_code_ttl, "pending" if deliver else "failed"),
            )
        return record_id, code, deliver

    def delivery_is_pending(self, record_id: str) -> bool:
        with self.connect() as db:
            return db.execute("SELECT 1 FROM email_verifications WHERE id = ? AND status = 'pending' AND expires_at > ?", (record_id, int(time.time()))).fetchone() is not None

    def finish_delivery(self, record_id: str, succeeded: bool):
        with self.connect() as db:
            db.execute("UPDATE email_verifications SET status = ? WHERE id = ? AND status = 'pending'", ("active" if succeeded else "failed", record_id))

    def apply_email_code(self, email: str, purpose: str, code: str, password_hash: str,
                         settings: Settings, username: str | None = None) -> UserResponse:
        """核验与账号变更共用写事务；错误计数必须提交后才抛错。"""
        now = int(time.time())
        result, duplicate = None, False
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM email_verifications WHERE email = ? AND purpose = ? AND status = 'active'", (email, purpose)).fetchone()
            valid = row is not None and row["expires_at"] > now and row["attempts"] < settings.email_code_max_attempts
            if valid:
                db.execute("UPDATE email_verifications SET attempts = attempts + 1 WHERE id = ?", (row["id"],))
                actual = email_code_digest(settings.email_code_secret, row["id"], email, purpose, code)
                valid = hmac.compare_digest(actual, row["code_hash"])
                if not valid and row["attempts"] + 1 >= settings.email_code_max_attempts:
                    db.execute("UPDATE email_verifications SET status = 'failed' WHERE id = ?", (row["id"],))
            if valid:
                if purpose == "register":
                    try:
                        cursor = db.execute("INSERT INTO users (username, password_hash, created_at, email, email_verified_at) VALUES (?, ?, ?, ?, ?)", (username, password_hash, now, email, now))
                        result = UserResponse(id=cursor.lastrowid, username=username, email=email)
                    except sqlite3.IntegrityError:
                        duplicate = True
                else:
                    user = db.execute("SELECT id, username FROM users WHERE id = ? AND email = ? AND email_verified_at IS NOT NULL", (row["user_id"], email)).fetchone()
                    if user:
                        db.execute("UPDATE users SET password_hash = ?, auth_version = auth_version + 1 WHERE id = ?", (password_hash, user["id"]))
                        db.execute("DELETE FROM sessions WHERE user_id = ?", (user["id"],))
                        result = UserResponse(id=user["id"], username=user["username"], email=email)
                if result:
                    db.execute("UPDATE email_verifications SET status = 'used' WHERE id = ?", (row["id"],))
                    db.execute("UPDATE email_verifications SET status = 'superseded' WHERE email = ? AND status IN ('pending', 'active')", (email,))
        if duplicate:
            raise UsernameTaken
        if result is None:
            raise InvalidEmailCode
        return result

    def reserve_auth_attempt(self, action: str, ip: str, username: str):
        """用事务限制并发请求；只信任连接 IP，不读取客户端转发头。"""
        now = int(time.time())
        buckets = [(f"ip:{ip}", 40)]
        buckets.append((f"user:{username.lower()}", 10) if action == "login" else (f"register:{ip}", 10))
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM auth_attempts WHERE reset_at <= ?", (now,))
            for bucket, limit in buckets:
                row = db.execute("SELECT count, reset_at FROM auth_attempts WHERE bucket = ?", (bucket,)).fetchone()
                if row and row["count"] >= limit:
                    raise TooManyAttempts(max(1, row["reset_at"] - now))
            for bucket, _ in buckets:
                db.execute(
                    "INSERT INTO auth_attempts VALUES (?, 1, ?) ON CONFLICT(bucket) DO UPDATE SET count = count + 1",
                    (bucket, now + 300),
                )

    def create_user(self, username: str, password_hash: str) -> UserResponse:
        try:
            with self.connect() as db:
                result = db.execute(
                    "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
                    (username, password_hash, int(time.time())),
                )
                return UserResponse(id=result.lastrowid, username=username)
        except sqlite3.IntegrityError as exc:
            raise UsernameTaken from exc

    def find_user(self, username: str):
        with self.connect() as db:
            return db.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()

    def find_user_by_id(self, user_id: int):
        with self.connect() as db:
            return db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()

    def reserve_account_attempt(self, user_id: int, ip: str):
        now = int(time.time())
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._reserve_buckets(db, [(f"account:user:{user_id}", 5, 300), (f"account:ip:{ip}", 20, 300)], now)

    @staticmethod
    def _check_account_snapshot(db, user_id: int, version: int, token: str):
        # 在写事务内再次检查会话和版本，防止旧密码核验与密码重置/退出之间的竞态。
        row = db.execute("SELECT users.* FROM users JOIN sessions ON sessions.user_id = users.id WHERE users.id = ? AND users.auth_version = ? AND sessions.token_hash = ? AND sessions.expires_at > ?", (user_id, version, token_digest(token), int(time.time()))).fetchone()
        if row is None:
            raise SessionChanged
        return row

    def rename_account(self, user_id: int, version: int, token: str, username: str):
        try:
            with self.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                row = self._check_account_snapshot(db, user_id, version, token)
                db.execute("UPDATE users SET username = ?, auth_version = auth_version + 1 WHERE id = ?", (username, user_id))
                return UserResponse(id=user_id, username=username, email=row["email"])
        except sqlite3.IntegrityError:
            raise UsernameTaken from None

    def change_account_password(self, user_id: int, version: int, token: str, password_hash: str, session_max_age: int):
        replacement_token = secrets.token_urlsafe(32)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._check_account_snapshot(db, user_id, version, token)
            db.execute("UPDATE users SET password_hash = ?, auth_version = auth_version + 1 WHERE id = ?", (password_hash, user_id))
            db.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
            # 改密、撤销旧登录与创建当前浏览器的新会话必须一同提交或回滚。
            db.execute("INSERT INTO sessions (token_hash, user_id, expires_at) VALUES (?, ?, ?)",
                       (token_digest(replacement_token), user_id, int(time.time()) + session_max_age))
            # 旧的邮箱重置挑战不能在主动改密后继续使用。
            if row["email"]:
                db.execute("UPDATE email_verifications SET status = 'superseded' WHERE email = ? AND status IN ('pending', 'active')", (row["email"],))
            return UserResponse(id=user_id, username=row["username"], email=row["email"]), replacement_token

    def create_session(self, user_id: int, max_age: int, old_token: str | None, expected_version: int | None = None) -> str:
        token = secrets.token_urlsafe(32)
        now = int(time.time())
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if expected_version is not None:
                row = db.execute("SELECT auth_version FROM users WHERE id = ?", (user_id,)).fetchone()
                if row is None or row["auth_version"] != expected_version:
                    raise SessionChanged
            db.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))
            if old_token:
                db.execute("DELETE FROM sessions WHERE token_hash = ?", (token_digest(old_token),))
            db.execute("INSERT INTO sessions VALUES (?, ?, ?)", (token_digest(token), user_id, now + max_age))
        return token

    def session_user(self, token: str | None) -> UserResponse | None:
        if not token or len(token) > 128:
            return None
        with self.connect() as db:
            row = db.execute(
                "SELECT users.id, users.username, users.email FROM sessions JOIN users ON users.id = sessions.user_id "
                "WHERE token_hash = ? AND expires_at > ?",
                (token_digest(token), int(time.time())),
            ).fetchone()
            return UserResponse(**dict(row)) if row else None

    def revoke_session(self, token: str | None):
        if token:
            with self.connect() as db:
                db.execute("DELETE FROM sessions WHERE token_hash = ?", (token_digest(token),))
