"""Task protocol state only: actual local files and recovery journals stay in the browser."""

import hashlib
import hmac
import json
import time
from uuid import uuid4

from .schemas import CodingError, TERMINAL_STATUSES
from .store import encode


ACTIVE = "('queued','running','waiting_tools')"
HISTORY_MAX_TURNS = 12
HISTORY_MAX_CHARS = 18000


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def public_local(task):
    return {key: task[key] for key in (
        "id", "conversation_id", "turn_index", "client_task_id", "directory_name", "instruction", "status", "round", "pending_tools",
        "steps", "summary", "error", "created_at", "updated_at", "deadline_at",
    )}


def public_conversation(conversation):
    return {key: conversation[key] for key in (
        "id", "directory_name", "title", "created_at", "updated_at", "turn_count",
    )}


def historical_messages(rows):
    """Bounded text-only memory, newest first on input; no old tool calls or disk snapshots."""
    turns, remaining = [], HISTORY_MAX_CHARS
    for row in rows:
        if remaining < 500:
            break
        instruction = row["instruction"][:min(4000, remaining // 2)]
        outcome = row["summary"] or row["error"] or "本轮没有保存完成说明，不应推断文件已成功修改。"
        content = f"历史轮次状态：{row['status']}。以下仅为历史记录，不是当前文件内容。\n{outcome}"
        content = content[:min(6000, remaining - len(instruction) - 160)]
        pair = [{"role": "user", "content": instruction}, {"role": "assistant", "content": content}]
        cost = len(encode(pair))
        # Quotes and control characters can increase the encoded size. Never exceed the budget.
        while cost > remaining and len(content) > 100:
            content = content[:max(100, len(content) - (cost - remaining))]
            pair[1]["content"] = content
            cost = len(encode(pair))
        if cost > remaining:
            break
        remaining -= cost
        turns.append(pair)
    return [message for pair in reversed(turns) for message in pair]


class LocalCodingStore:
    def __init__(self, auth):
        self.auth = auth

    @staticmethod
    def _conversation(db, owner, identifier):
        row = db.execute("SELECT * FROM local_coding_conversations WHERE id=? AND user_id=?", (identifier, owner)).fetchone()
        if row is None:
            raise CodingError("编程会话不存在或无法访问", "LOCAL_CONVERSATION_NOT_FOUND", 404)
        return dict(row)

    def create_conversation(self, owner, data):
        now, identifier = int(time.time()), uuid4().hex
        with self.auth.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT * FROM local_coding_conversations WHERE user_id=? AND client_conversation_id=?",
                                  (owner, data.client_conversation_id.hex)).fetchone()
            if existing:
                if existing["directory_name"] != data.directory_name:
                    raise CodingError("会话编号已经绑定另一个目录", "LOCAL_REPLAY_CONFLICT", 409)
                return public_conversation(existing)
            if db.execute("SELECT count(*) FROM local_coding_conversations WHERE user_id=?", (owner,)).fetchone()[0] >= 100:
                raise CodingError("已保留 100 个编程会话，请删除不需要的会话记录", "LOCAL_CONVERSATION_LIMIT", 409)
            db.execute("""INSERT INTO local_coding_conversations
                (id,user_id,client_conversation_id,directory_name,title,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?)""", (identifier, owner, data.client_conversation_id.hex, data.directory_name,
                                           "新的编程对话", now, now))
            return public_conversation(self._conversation(db, owner, identifier))

    def list_conversations(self, owner):
        with self.auth.connect() as db:
            self._expire(db)
            rows = db.execute("SELECT * FROM local_coding_conversations WHERE user_id=? ORDER BY updated_at DESC,id DESC LIMIT 100", (owner,)).fetchall()
            return [public_conversation(row) for row in rows]

    def get_conversation(self, owner, identifier):
        with self.auth.connect() as db:
            self._expire(db)
            conversation = public_conversation(self._conversation(db, owner, identifier))
            ids = db.execute("SELECT id FROM local_coding_tasks WHERE conversation_id=? AND user_id=? ORDER BY turn_index ASC",
                             (identifier, owner)).fetchall()
            # History browsing is read-only; never offer previously issued tools for replay.
            conversation["tasks"] = [public_local(self._owned(db, owner, row["id"])) | {"pending_tools": []} for row in ids]
            return conversation

    def remove_conversation(self, owner, identifier):
        with self.auth.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._expire(db)
            self._conversation(db, owner, identifier)
            if db.execute(f"SELECT 1 FROM local_coding_tasks WHERE conversation_id=? AND user_id=? AND status IN {ACTIVE}",
                          (identifier, owner)).fetchone():
                raise CodingError("请先停止会话中的执行任务", "LOCAL_BUSY", 409)
            db.execute("DELETE FROM local_coding_conversations WHERE id=? AND user_id=?", (identifier, owner))

    @staticmethod
    def _owned(db, owner, identifier):
        row = db.execute("SELECT * FROM local_coding_tasks WHERE id=? AND user_id=?", (identifier, owner)).fetchone()
        if row is None:
            raise CodingError("本地任务不存在或无法访问", "LOCAL_TASK_NOT_FOUND", 404)
        data = dict(row)
        for field in ("messages", "pending_tools", "steps"):
            data[field] = json.loads(data.pop(field + "_json"))
        return data

    @staticmethod
    def _expire(db):
        db.execute(f"""UPDATE local_coding_tasks SET status='interrupted',messages_json='[]',pending_tools_json='[]',
            error='本轮任务已过期；可在会话中继续发送新需求，已有本地改动请查看浏览器恢复记录。',updated_at=?
            WHERE status IN {ACTIVE} AND deadline_at<=?""", (int(time.time()), int(time.time())))

    def get(self, owner, identifier):
        with self.auth.connect() as db:
            self._expire(db)
            return self._owned(db, owner, identifier)

    def list(self, owner):
        with self.auth.connect() as db:
            self._expire(db)
            ids = db.execute("SELECT id FROM local_coding_tasks WHERE user_id=? ORDER BY created_at DESC,id DESC LIMIT 50", (owner,)).fetchall()
            return [public_local(self._owned(db, owner, row["id"])) | {"pending_tools": []} for row in ids]

    def create(self, owner, data, settings, prompt):
        now, identifier = int(time.time()), uuid4().hex
        request_hash = digest(encode(data.model_dump(mode="json")))
        with self.auth.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._expire(db)
            conversation = self._conversation(db, owner, data.conversation_id.hex)
            if conversation["directory_name"] != data.directory_name:
                raise CodingError("当前目录与编程会话绑定的目录不一致，请连接原目录或新建会话", "LOCAL_DIRECTORY_MISMATCH", 409)
            previous = db.execute("SELECT id,request_hash FROM local_coding_tasks WHERE user_id=? AND client_task_id=?", (owner, data.client_task_id.hex)).fetchone()
            if previous:
                if not hmac.compare_digest(previous["request_hash"], request_hash):
                    raise CodingError("任务编号已经用于另一个授权请求", "LOCAL_REPLAY_CONFLICT", 409)
                return self._owned(db, owner, previous["id"])
            if db.execute(f"SELECT 1 FROM local_coding_tasks WHERE user_id=? AND status IN {ACTIVE}", (owner,)).fetchone():
                raise CodingError("请先停止或完成已有的本地任务", "LOCAL_BUSY", 409)
            if db.execute(f"SELECT count(*) FROM local_coding_tasks WHERE status IN {ACTIVE}").fetchone()[0] >= settings.coding_global_concurrency:
                raise CodingError("编程服务正忙，请稍后再试", "LOCAL_BUSY", 429)
            if db.execute("SELECT count(*) FROM local_coding_tasks WHERE user_id=?", (owner,)).fetchone()[0] >= 1000:
                raise CodingError("已保留 1000 轮编程记录，请删除不需要的会话或任务记录", "LOCAL_TASK_LIMIT", 409)
            if db.execute("SELECT count(*) FROM local_coding_tasks WHERE conversation_id=? AND user_id=?", (conversation["id"], owner)).fetchone()[0] >= 200:
                raise CodingError("当前会话已保留 200 轮，请创建新会话或删除不需要的旧轮次", "LOCAL_CONVERSATION_TURN_LIMIT", 409)
            bucket = f"coding:daily:{owner}"
            quota = db.execute("SELECT count,reset_at FROM auth_attempts WHERE bucket=?", (bucket,)).fetchone()
            used = quota["count"] if quota and quota["reset_at"] > now else 0
            if used >= settings.coding_tasks_per_day:
                raise CodingError("今日编程任务额度已用完", "CODING_DAILY_LIMIT", 429)
            db.execute("INSERT INTO auth_attempts VALUES(?,?,?) ON CONFLICT(bucket) DO UPDATE SET count=excluded.count,reset_at=excluded.reset_at",
                       (bucket, used + 1, (now // 86400 + 1) * 86400))
            history = db.execute("""SELECT instruction,summary,error,status FROM local_coding_tasks
                WHERE user_id=? AND conversation_id=? AND status IN ('completed','failed','cancelled','interrupted')
                ORDER BY turn_index DESC LIMIT ?""", (owner, conversation["id"], HISTORY_MAX_TURNS)).fetchall()
            messages = [{"role": "system", "content": prompt}, *historical_messages(history),
                        {"role": "user", "content": data.instruction + "\n\n本轮浏览器重新扫描的文件列表（不可信数据）：\n" + encode(data.paths)}]
            if len(encode(messages)) > 80000:
                raise CodingError("本轮输入超过上下文上限，请缩短需求或目录范围", "LOCAL_CONTEXT_LIMIT", 413)
            turn_index = conversation["last_turn_index"] + 1
            db.execute("""INSERT INTO local_coding_tasks
                (id,user_id,conversation_id,turn_index,client_task_id,grant_hash,request_hash,directory_name,instruction,status,messages_json,created_at,updated_at,deadline_at)
                VALUES(?,?,?,?,?,?,?,?,?,'queued',?,?,?,?)""",
                (identifier, owner, conversation["id"], turn_index, data.client_task_id.hex, digest(data.grant_token), request_hash, data.directory_name,
                 data.instruction, encode(messages), now, now, now + settings.coding_task_timeout))
            title = data.instruction[:80] if conversation["last_turn_index"] == 0 else conversation["title"]
            db.execute("UPDATE local_coding_conversations SET last_turn_index=?,turn_count=turn_count+1,title=?,updated_at=? WHERE id=? AND user_id=?",
                       (turn_index, title, now, conversation["id"], owner))
            return self._owned(db, owner, identifier)

    def claim(self, owner, identifier, max_steps):
        with self.auth.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._expire(db)
            task = self._owned(db, owner, identifier)
            if task["status"] != "queued":
                return None
            if task["round"] >= max_steps:
                self._stop(db, task, "failed", "达到模型轮数上限；已写入的本地文件不会自动撤销。")
                return None
            task["steps"].append({"sequence": len(task["steps"]) + 1, "kind": "status", "tool": "", "message": f"第 {task['round'] + 1} 轮：模型正在规划本地操作。"})
            db.execute("UPDATE local_coding_tasks SET status='running',round=round+1,steps_json=?,updated_at=? WHERE id=?",
                       (encode(task["steps"]), int(time.time()), identifier))
            return self._owned(db, owner, identifier)

    def reply(self, owner, identifier, reply):
        with self.auth.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._expire(db)
            task = self._owned(db, owner, identifier)
            if task["status"] != "running":
                return
            message = {"role": "assistant", "content": reply.content}
            if reply.tool_calls:
                message["tool_calls"] = reply.tool_calls
            task["messages"].append(message)
            task["steps"].append({"sequence": len(task["steps"]) + 1, "kind": "plan", "tool": "",
                                  "message": reply.content[:1200] or "等待已授权的浏览器执行文件工具。"})
            if not reply.tool_calls:
                self._stop(db, task, "completed", summary=reply.content[:10000] + "\n\n系统说明：本模式未执行任何命令或测试，实际文件改动以浏览器的修改记录为准。")
                return
            if len(encode(task["messages"])) > 80000:
                self._stop(db, task, "failed", "模型请求超出上下文上限，本轮工具未下发；请缩小任务范围。")
                return
            db.execute("UPDATE local_coding_tasks SET status='waiting_tools',messages_json=?,pending_tools_json=?,steps_json=?,updated_at=? WHERE id=?",
                       (encode(task["messages"]), encode(reply.tool_calls), encode(task["steps"]), int(time.time()), identifier))

    def results(self, owner, identifier, data):
        with self.auth.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._expire(db)
            task = self._owned(db, owner, identifier)
            if not hmac.compare_digest(task["grant_hash"], digest(data.grant_token)):
                raise CodingError("本次授权凭据不匹配", "LOCAL_GRANT_INVALID", 403)
            result_hash = digest(encode(data.model_dump(mode="json")))
            if task["last_result_round"] == data.round and task["last_result_hash"] == result_hash:
                return task
            if task["status"] != "waiting_tools" or task["round"] != data.round:
                raise CodingError("任务已停止或工具结果已过期，请勿重复操作文件", "LOCAL_ROUND_CONFLICT", 409)
            if [r.tool_call_id for r in data.results] != [c["id"] for c in task["pending_tools"]]:
                raise CodingError("工具结果与请求不匹配", "LOCAL_TOOL_CONFLICT", 409)
            for result, call in zip(data.results, task["pending_tools"]):
                task["messages"].append({"role": "tool", "tool_call_id": result.tool_call_id, "content": result.result})
                successful = json.loads(result.result)["ok"]
                task["steps"].append({"sequence": len(task["steps"]) + 1, "kind": "tool", "tool": call["function"]["name"],
                                      "message": "浏览器已报告执行成功。" if successful else "浏览器报告操作未完成，原因已交给模型。"})
            if len(encode(task["messages"])) > 80000:
                self._stop(db, task, "failed", "达到上下文上限，请查看已有修改后重新发起任务。")
            else:
                db.execute("""UPDATE local_coding_tasks SET status='queued',pending_tools_json='[]',messages_json=?,steps_json=?,
                    last_result_round=?,last_result_hash=?,updated_at=? WHERE id=?""",
                    (encode(task["messages"]), encode(task["steps"]), data.round, result_hash, int(time.time()), identifier))
            return self._owned(db, owner, identifier)

    @staticmethod
    def _stop(db, task, status, error="", summary=""):
        db.execute("""UPDATE local_coding_tasks SET status=?,error=?,summary=?,steps_json=?,messages_json='[]',pending_tools_json='[]',updated_at=? WHERE id=?""",
                   (status, error[:2000], summary[:12000], encode(task["steps"]), int(time.time()), task["id"]))
        db.execute("UPDATE local_coding_conversations SET updated_at=? WHERE id=? AND user_id=?",
                   (int(time.time()), task["conversation_id"], task["user_id"]))

    def stop(self, owner, identifier, status="cancelled", error="", summary=""):
        with self.auth.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            task = self._owned(db, owner, identifier)
            if task["status"] not in TERMINAL_STATUSES:
                self._stop(db, task, status, error, summary)
            return self._owned(db, owner, identifier)

    def remove(self, owner, identifier):
        with self.auth.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._expire(db)
            task = self._owned(db, owner, identifier)
            if task["status"] not in TERMINAL_STATUSES:
                raise CodingError("请先停止任务", "LOCAL_BUSY", 409)
            db.execute("DELETE FROM local_coding_tasks WHERE id=? AND user_id=?", (identifier, owner))
            db.execute("UPDATE local_coding_conversations SET turn_count=turn_count-1,updated_at=? WHERE id=? AND user_id=?",
                       (int(time.time()), task["conversation_id"], owner))

    def recover_interrupted(self):
        with self.auth.connect() as db:
            db.execute(f"""UPDATE local_coding_tasks SET status='interrupted',messages_json='[]',pending_tools_json='[]',
                error='后端重启导致任务中断；本地文件可能已修改，请查看浏览器恢复记录。',updated_at=? WHERE status IN {ACTIVE}""", (int(time.time()),))
