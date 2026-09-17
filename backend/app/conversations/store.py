import time
from uuid import uuid4

from app.auth.store import AuthStore
from app.conversations.schemas import SendMessage
from app.schemas import ChatMessage, ChatResponse


class HistoryError(Exception):
    def __init__(self, message: str, code="HISTORY_CONFLICT", status=409, retry_after=0):
        super().__init__(message)
        self.code, self.status, self.retry_after = code, status, retry_after


class ConversationStore:
    def __init__(self, auth: AuthStore):
        self.auth = auth

    @staticmethod
    def owned(db, user_id, conversation_id):
        row = db.execute("SELECT * FROM conversations WHERE id = ? AND user_id = ?", (conversation_id, user_id)).fetchone()
        if row is None:
            raise HistoryError("会话不存在或无法访问", "CONVERSATION_NOT_FOUND", 404)
        return dict(row)

    @staticmethod
    def recover(db, user_id):
        # 崩溃或重启遗留的占位问题可重新尝试，旧请求不能覆盖新尝试。
        db.execute("""UPDATE messages SET status = 'failed', lease_expires_at = 0,
            error_code = 'TURN_INTERRUPTED', error_message = '上次回答被中断，可以重新尝试。'
            WHERE status = 'pending' AND lease_expires_at <= ?
            AND conversation_id IN (SELECT id FROM conversations WHERE user_id = ?)""", (int(time.time()), user_id))

    def create(self, user_id, identifier):
        now = int(time.time())
        with self.auth.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT user_id FROM conversations WHERE id = ?", (identifier,)).fetchone()
            if existing and existing["user_id"] != user_id:
                raise HistoryError("会话编号不可用，请重新新建对话", "CONVERSATION_ID_CONFLICT")
            db.execute("INSERT OR IGNORE INTO conversations (id, user_id, title, created_at, updated_at) VALUES (?, ?, '新的对话', ?, ?)", (identifier, user_id, now, now))
            return self.owned(db, user_id, identifier)

    def list(self, user_id, limit, cursor=None):
        params = [user_id]
        condition = ""
        if cursor:
            stamp, identifier = cursor.split(":")
            condition = " AND (updated_at < ? OR (updated_at = ? AND id < ?))"
            params.extend([int(stamp), int(stamp), identifier])
        with self.auth.connect() as db:
            rows = db.execute("SELECT * FROM conversations WHERE user_id = ?" + condition + " ORDER BY updated_at DESC, id DESC LIMIT ?", (*params, limit + 1)).fetchall()
        items = [dict(row) for row in rows[:limit]]
        next_cursor = f"{items[-1]['updated_at']}:{items[-1]['id']}" if len(rows) > limit else None
        return {"items": items, "next_cursor": next_cursor}

    def read(self, user_id, conversation_id, limit=50, before=None):
        with self.auth.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            conversation = self.owned(db, user_id, conversation_id)
            self.recover(db, user_id)
            rows = db.execute("SELECT * FROM messages WHERE conversation_id = ? AND sequence < ? ORDER BY sequence DESC LIMIT ?", (conversation_id, before or conversation["last_sequence"] + 1, limit + 1)).fetchall()
            items = [dict(row) for row in reversed(rows[:limit])]
            return {"conversation": conversation, "messages": items, "next_before": items[0]["sequence"] if len(rows) > limit else None}

    def rename(self, user_id, conversation_id, title):
        with self.auth.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self.owned(db, user_id, conversation_id)
            db.execute("UPDATE conversations SET title = ?, title_custom = 1 WHERE id = ? AND user_id = ?", (title, conversation_id, user_id))
            return self.owned(db, user_id, conversation_id)

    def delete(self, user_id, conversation_id):
        with self.auth.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self.owned(db, user_id, conversation_id)
            self.recover(db, user_id)
            if db.execute("SELECT 1 FROM messages WHERE conversation_id = ? AND status = 'pending'", (conversation_id,)).fetchone():
                raise HistoryError("此会话仍在回答中，请停止等待后稍后再删除", "CONVERSATION_BUSY", 409, 2)
            db.execute("DELETE FROM conversations WHERE id = ? AND user_id = ?", (conversation_id, user_id))

    def begin(self, user_id, conversation_id, data: SendMessage, request_id, timeout):
        now = int(time.time())
        client_id = data.client_message_id.hex
        with self.auth.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            conversation = self.owned(db, user_id, conversation_id)
            self.recover(db, user_id)
            existing = db.execute("SELECT * FROM messages WHERE conversation_id = ? AND client_message_id = ? AND role = 'user'", (conversation_id, client_id)).fetchone()
            if existing:
                if existing["content"] != data.content:
                    raise HistoryError("同一请求编号不能用于不同问题", "MESSAGE_ID_CONFLICT")
                if existing["status"] == "completed":
                    reply = db.execute("SELECT * FROM messages WHERE reply_to_id = ?", (existing["id"],)).fetchone()
                    return {"cached": {"conversation": conversation, "user_message": dict(existing), "assistant_message": dict(reply), "replayed": True}}
                if existing["status"] == "pending":
                    raise HistoryError("这个问题仍在处理中，请稍后刷新会话", "TURN_PENDING", 409, 2)
                if not data.retry:
                    raise HistoryError("这个问题上次回答失败，请使用重新尝试", "TURN_FAILED")
                if existing["sequence"] != conversation["last_sequence"]:
                    raise HistoryError("会话已有后续消息，请把问题作为新消息发送", "STALE_CONVERSATION")
                message_id = existing["id"]
                sequence = existing["sequence"]
                db.execute("UPDATE messages SET status = 'pending', request_id = ?, lease_expires_at = ?, error_code = NULL, error_message = NULL WHERE id = ?", (request_id, now + timeout + 60, message_id))
            else:
                # 原始请求可能从未到达后端，允许 retry 同一编号补交；仍检查序号。
                if db.execute("SELECT 1 FROM messages WHERE conversation_id = ? AND status = 'pending'", (conversation_id,)).fetchone():
                    raise HistoryError("这个会话正在回答其他问题，请稍后刷新", "TURN_PENDING", 409, 2)
                if data.expected_last_sequence != conversation["last_sequence"]:
                    raise HistoryError("会话已在其他窗口更新，请刷新后再发送", "STALE_CONVERSATION")
                message_id, sequence = uuid4().hex, conversation["last_sequence"] + 1
                db.execute("""INSERT INTO messages (id, conversation_id, client_message_id, sequence, role, content, status, request_id, lease_expires_at, created_at)
                    VALUES (?, ?, ?, ?, 'user', ?, 'pending', ?, ?, ?)""", (message_id, conversation_id, client_id, sequence, data.content, request_id, now + timeout + 60, now))
                db.execute("UPDATE conversations SET last_sequence = ?, updated_at = ? WHERE id = ?", (sequence, now, conversation_id))
                if conversation["last_sequence"] == 0 and not conversation["title_custom"]:
                    db.execute("UPDATE conversations SET title = ? WHERE id = ?", (" ".join(data.content.split())[:30], conversation_id))
            rows = db.execute("SELECT role, content FROM messages WHERE conversation_id = ? AND sequence < ? AND status = 'completed' ORDER BY sequence DESC LIMIT 51", (conversation_id, sequence)).fetchall()
            history = [ChatMessage(role=row["role"], content=row["content"]) for row in reversed(rows[:50])]
            history.append(ChatMessage(role="user", content=data.content))
            return {"message_id": message_id, "messages": history, "trimmed": len(rows) > 50}

    def complete(self, user_id, conversation_id, message_id, request_id, answer: ChatResponse):
        now = int(time.time())
        with self.auth.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self.owned(db, user_id, conversation_id)
            row = db.execute("SELECT * FROM messages WHERE id = ? AND conversation_id = ? AND status = 'pending' AND request_id = ? AND lease_expires_at > ?", (message_id, conversation_id, request_id, now)).fetchone()
            if row is None:
                raise HistoryError("此回答已失效，请刷新会话确认最新状态", "TURN_REPLACED")
            db.execute("UPDATE messages SET status = 'completed', lease_expires_at = 0 WHERE id = ?", (message_id,))
            reply_id = uuid4().hex
            db.execute("""INSERT INTO messages (id, conversation_id, client_message_id, sequence, role, content, status, model, request_id, reply_to_id, context_trimmed, answer_truncated, created_at)
                VALUES (?, ?, ?, ?, 'assistant', ?, 'completed', ?, ?, ?, ?, ?, ?)""", (reply_id, conversation_id, row["client_message_id"], row["sequence"] + 1, answer.content, answer.model, request_id, message_id, int(answer.context_trimmed), int(answer.answer_truncated), now))
            db.execute("UPDATE conversations SET last_sequence = ?, updated_at = ? WHERE id = ?", (row["sequence"] + 1, now, conversation_id))
            return {"conversation": self.owned(db, user_id, conversation_id), "user_message": dict(db.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()), "assistant_message": dict(db.execute("SELECT * FROM messages WHERE id = ?", (reply_id,)).fetchone()), "replayed": False}

    def fail(self, user_id, conversation_id, request_id, code, message):
        with self.auth.connect() as db:
            db.execute("""UPDATE messages SET status = 'failed', lease_expires_at = 0, error_code = ?, error_message = ?
                WHERE conversation_id = ? AND request_id = ? AND status = 'pending'
                AND conversation_id IN (SELECT id FROM conversations WHERE user_id = ?)""", (code, message, conversation_id, request_id, user_id))
