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

    def reserve(self, identifier: str, user_id: int, input_chars: int, settings: Settings, model=None,
                source="default", lease_seconds=None):
        # This value comes from a server-selected adapter, never an HTTP field or model name.
        if source not in {"default", "custom", "embedding"}:
            raise ValueError("Unknown model call source")
        now = int(time.time())
        day_start = now // 86400 * 86400
        embedding = source == "embedding"
        with self.auth.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM model_calls WHERE created_at < ? AND lease_expires_at <= ?", (now - 30 * 86400, now))
            db.execute("UPDATE model_calls SET state = 'abandoned', finished_at = ?, error_code = 'LEASE_EXPIRED' WHERE state = 'running' AND lease_expires_at <= ?", (now, now))
            # An embedding is a child of a metered chat turn, so it uses a separate
            # bounded pool rather than conflicting with its own parent lease.
            active = db.execute("SELECT user_id, lease_expires_at FROM model_calls WHERE state = 'running' AND lease_expires_at > ? AND (source = 'embedding') = ?", (now, embedding)).fetchall()
            own = [row for row in active if row["user_id"] == user_id]
            if len(own) >= settings.chat_user_concurrency:
                raise ChatLimit("你还有回答正在处理中，请稍后再试", "CHAT_USER_BUSY", min(5, min(row["lease_expires_at"] for row in own) - now))
            if len(active) >= settings.chat_global_concurrency:
                raise ChatLimit("当前服务较忙，请稍后再试", "CHAT_SERVER_BUSY", min(5, min(row["lease_expires_at"] for row in active) - now))
            recent = db.execute("SELECT count(*), min(created_at) FROM model_calls WHERE user_id = ? AND created_at > ? AND (source = 'embedding') = ?", (user_id, now - 60, embedding)).fetchone()
            if recent[0] >= settings.chat_requests_per_minute:
                raise ChatLimit("提问过于频繁，请稍后重试", "CHAT_RATE_LIMIT", recent[1] + 60 - now)
            if source != "custom":
                sources = ("embedding", "embedding") if embedding else ("default", "legacy")
                day = db.execute("SELECT count(*), coalesce(sum(user_id = ?), 0) FROM model_calls WHERE created_at >= ? AND source IN (?, ?)", (user_id, day_start, *sources)).fetchone()
                user_limit = settings.rag.requests_per_day if embedding else settings.chat_requests_per_day
                global_limit = settings.rag.global_requests_per_day if embedding else settings.chat_global_requests_per_day
                if day[1] >= user_limit:
                    raise ChatLimit("今日资料向量调用额度已用完" if embedding else "今日默认模型额度已用完，可使用个人 API 或在额度重置后继续", "RAG_DAILY_LIMIT" if embedding else "CHAT_DAILY_LIMIT", day_start + 86400 - now)
                if day[0] >= global_limit:
                    raise ChatLimit("今日全站资料向量调用额度已用完" if embedding else "今日全站默认模型额度已用完，可使用个人 API 或在额度重置后继续", "RAG_GLOBAL_DAILY_LIMIT" if embedding else "CHAT_GLOBAL_DAILY_LIMIT", day_start + 86400 - now)
            lease = lease_seconds if lease_seconds is not None else settings.chat_timeout + 30
            db.execute("INSERT INTO model_calls (request_id, user_id, model, created_at, lease_expires_at, state, input_chars, source) VALUES (?, ?, ?, ?, ?, 'running', ?, ?)", (identifier, user_id, model or settings.qwen_model, now, now + lease, input_chars, source))

    def finish(self, identifier: str, state: str, duration_ms: int, *, output_chars=None, input_tokens=None, output_tokens=None, error_code=None, input_chars=None):
        with self.auth.connect() as db:
            db.execute("UPDATE model_calls SET state = ?, finished_at = ?, duration_ms = ?, output_chars = ?, input_tokens = ?, output_tokens = ?, error_code = ?, input_chars = coalesce(?, input_chars) WHERE request_id = ? AND state = 'running'", (state, int(time.time()), duration_ms, output_chars, input_tokens, output_tokens, error_code, input_chars, identifier))

    def usage(self, user_id: int, settings: Settings):
        now = int(time.time())
        day_start = now // 86400 * 86400
        with self.auth.connect() as db:
            rows = db.execute("""SELECT CASE WHEN source='legacy' THEN 'default' ELSE source END AS category,
                count(*) AS requests, coalesce(sum(state='succeeded'),0) AS succeeded,
                sum(input_tokens) AS input_tokens, sum(output_tokens) AS output_tokens,
                sum(source='legacy') AS legacy_requests FROM model_calls
                WHERE user_id=? AND created_at>=? GROUP BY category""", (user_id, day_start)).fetchall()
        categories = {row["category"]: dict(row) for row in rows}
        def summary(source, limit):
            row = categories.get(source, {})
            return {"requests": row.get("requests", 0), "succeeded": row.get("succeeded", 0),
                    "input_tokens": row.get("input_tokens"), "output_tokens": row.get("output_tokens"),
                    "daily_limit": limit}
        # Keep original top-level fields as the default-model allowance for older clients.
        return {**summary("default", settings.chat_requests_per_day),
                "custom": summary("custom", None), "embedding": summary("embedding", settings.rag.requests_per_day),
                "legacy_requests": categories.get("default", {}).get("legacy_requests", 0),
                "resets_at": day_start + 86400}
