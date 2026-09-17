import time

from app.auth.store import AuthStore
from app.config import Settings


class ChatLimit(Exception):
    def __init__(self, message: str, code: str, retry_after: int):
        super().__init__(message)
        self.code, self.retry_after = code, max(1, retry_after)


class ModelCallStore:
    def __init__(self, auth: AuthStore):
        self.auth = auth

    def reserve(self, identifier: str, user_id: int, input_chars: int, settings: Settings):
        now = int(time.time())
        day_start = now // 86400 * 86400
        with self.auth.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM model_calls WHERE created_at < ? AND lease_expires_at <= ?", (now - 30 * 86400, now))
            db.execute("UPDATE model_calls SET state = 'abandoned', finished_at = ?, error_code = 'LEASE_EXPIRED' WHERE state = 'running' AND lease_expires_at <= ?", (now, now))
            active = db.execute("SELECT user_id, lease_expires_at FROM model_calls WHERE state = 'running' AND lease_expires_at > ?", (now,)).fetchall()
            own = [row for row in active if row["user_id"] == user_id]
            if len(own) >= settings.chat_user_concurrency:
                raise ChatLimit("你还有回答正在处理中，请稍后再试", "CHAT_USER_BUSY", min(5, min(row["lease_expires_at"] for row in own) - now))
            if len(active) >= settings.chat_global_concurrency:
                raise ChatLimit("当前服务较忙，请稍后再试", "CHAT_SERVER_BUSY", min(5, min(row["lease_expires_at"] for row in active) - now))
            recent = db.execute("SELECT count(*), min(created_at) FROM model_calls WHERE user_id = ? AND created_at > ?", (user_id, now - 60)).fetchone()
            if recent[0] >= settings.chat_requests_per_minute:
                raise ChatLimit("提问过于频繁，请稍后重试", "CHAT_RATE_LIMIT", recent[1] + 60 - now)
            day = db.execute("SELECT count(*), coalesce(sum(user_id = ?), 0) FROM model_calls WHERE created_at >= ?", (user_id, day_start)).fetchone()
            if day[1] >= settings.chat_requests_per_day:
                raise ChatLimit("今日提问额度已用完，请在额度重置后继续", "CHAT_DAILY_LIMIT", day_start + 86400 - now)
            if day[0] >= settings.chat_global_requests_per_day:
                raise ChatLimit("今日服务调用额度已用完，请在额度重置后继续", "CHAT_GLOBAL_DAILY_LIMIT", day_start + 86400 - now)
            db.execute("INSERT INTO model_calls (request_id, user_id, model, created_at, lease_expires_at, state, input_chars) VALUES (?, ?, ?, ?, ?, 'running', ?)", (identifier, user_id, settings.qwen_model, now, now + settings.chat_timeout + 30, input_chars))

    def finish(self, identifier: str, state: str, duration_ms: int, *, output_chars=None, input_tokens=None, output_tokens=None, error_code=None):
        with self.auth.connect() as db:
            db.execute("UPDATE model_calls SET state = ?, finished_at = ?, duration_ms = ?, output_chars = ?, input_tokens = ?, output_tokens = ?, error_code = ? WHERE request_id = ? AND state = 'running'", (state, int(time.time()), duration_ms, output_chars, input_tokens, output_tokens, error_code, identifier))

    def usage(self, user_id: int, settings: Settings):
        now = int(time.time())
        day_start = now // 86400 * 86400
        with self.auth.connect() as db:
            row = db.execute("SELECT count(*) AS requests, coalesce(sum(state = 'succeeded'), 0) AS succeeded, sum(input_tokens) AS input_tokens, sum(output_tokens) AS output_tokens FROM model_calls WHERE user_id = ? AND created_at >= ?", (user_id, day_start)).fetchone()
        return {**dict(row), "daily_limit": settings.chat_requests_per_day, "resets_at": day_start + 86400}
