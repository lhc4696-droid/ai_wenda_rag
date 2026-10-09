"""Owner-scoped projects and durable task snapshots, backed by the existing SQLite DB.

Projects keep immutable imported source. Each task owns baseline_files and files
snapshots; a follow-up task can branch from a terminal task without mutating it.
create_task returns (internal_task, replayed), all get/save/cancel methods return
internal records. list methods return public summaries. No API returns source text
implicitly: the routes use public_project/public_task and explicit file endpoints.
"""

import json
import time
from uuid import uuid4

from app.auth.store import AuthStore, TooManyAttempts
from app.coding.schemas import ACTIVE_STATUSES, TERMINAL_STATUSES, CodingError, CreateTask, public_project, public_task
from app.coding.workspace import MAX_PROJECTS, validate_files


MAX_TASKS_PER_USER = 100


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def retire_zip_tasks(auth: AuthStore):
    """Retire unfinished legacy jobs on startup without deleting source or results."""
    with auth.connect() as db:
        cursor = db.execute("""UPDATE coding_tasks SET status='interrupted',cancel_requested=1,
            error='ZIP 工作台已停用，已有任务副本仍保留在数据库中。',updated_at=?
            WHERE status IN ('queued','running')""", (int(time.time()),))
        return cursor.rowcount


class CodingStore:
    def __init__(self, auth: AuthStore):
        self.auth = auth

    def reserve_import(self, user_id):
        """Charge import attempts before multipart parsing, including rejected ZIPs."""
        with self.auth.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                self.auth._reserve_buckets(db, [
                    (f"coding:import:{user_id}", 10, 300),
                    ("coding:import:global", 40, 300),
                ], int(time.time()))
            except TooManyAttempts as error:
                raise CodingError(f"项目导入过于频繁，请约 {error.retry_after} 秒后重试", "CODING_IMPORT_RATE_LIMIT", 429) from None

    @staticmethod
    def _project(row):
        result = dict(row)
        result["source_files"] = json.loads(result.pop("source_files_json"))
        result["warnings"] = json.loads(result.pop("warnings_json"))
        return result

    @staticmethod
    def _task(row):
        result = dict(row)
        for field in ("files", "baseline_files", "steps", "checks"):
            result[field] = json.loads(result.pop(f"{field}_json"))
        result.pop("request_json", None)
        return result

    @staticmethod
    def _owned_project(db, user_id, identifier):
        row = db.execute("SELECT * FROM coding_projects WHERE id = ? AND user_id = ?", (identifier, user_id)).fetchone()
        if row is None:
            raise CodingError("项目不存在或无法访问", "CODING_PROJECT_NOT_FOUND", 404)
        return row

    @staticmethod
    def _owned_task(db, user_id, identifier):
        row = db.execute("SELECT * FROM coding_tasks WHERE id = ? AND user_id = ?", (identifier, user_id)).fetchone()
        if row is None:
            raise CodingError("编程任务不存在或无法访问", "CODING_TASK_NOT_FOUND", 404)
        return row

    def list_projects(self, user_id):
        with self.auth.connect() as db:
            rows = db.execute("SELECT * FROM coding_projects WHERE user_id = ? ORDER BY updated_at DESC, id DESC", (user_id,)).fetchall()
        return [public_project(self._project(row)) for row in rows]

    def create_project(self, user_id, name, files, warnings):
        validate_files(files)
        name = " ".join(str(name).split())[:80] or "Python 项目"
        now, identifier = int(time.time()), uuid4().hex
        with self.auth.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            count = db.execute("SELECT count(*) FROM coding_projects WHERE user_id = ?", (user_id,)).fetchone()[0]
            if count >= MAX_PROJECTS:
                raise CodingError(f"最多保留 {MAX_PROJECTS} 个项目，请先删除不再使用的项目", "CODING_PROJECT_LIMIT", 409)
            db.execute("""INSERT INTO coding_projects
                (id,user_id,name,source_files_json,warnings_json,created_at,updated_at) VALUES (?,?,?,?,?,?,?)""",
                (identifier, user_id, name, encode(files), encode(warnings), now, now))
            return self._project(self._owned_project(db, user_id, identifier))

    def get_project(self, user_id, identifier):
        with self.auth.connect() as db:
            return self._project(self._owned_project(db, user_id, identifier))

    def delete_project(self, user_id, identifier):
        with self.auth.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._owned_project(db, user_id, identifier)
            if db.execute("SELECT 1 FROM coding_tasks WHERE project_id = ? AND user_id = ? AND status IN ('queued','running')", (identifier, user_id)).fetchone():
                raise CodingError("项目仍有任务运行中，请取消并等待停止后再删除", "CODING_PROJECT_BUSY", 409)
            db.execute("DELETE FROM coding_projects WHERE id = ? AND user_id = ?", (identifier, user_id))

    def list_tasks(self, user_id, project_id):
        with self.auth.connect() as db:
            self._owned_project(db, user_id, project_id)
            rows = db.execute("""SELECT id,project_id,status,instruction,summary,error,created_at,updated_at,
                base_task_id,cancel_requested FROM coding_tasks WHERE project_id = ? AND user_id = ?
                ORDER BY created_at DESC,id DESC""", (project_id, user_id)).fetchall()
        return [public_task(dict(row), detail=False) for row in rows]

    def create_task(self, user_id, project_id, data: CreateTask, global_limit=4, daily_limit=20):
        now, identifier = int(time.time()), uuid4().hex
        base_id = data.base_task_id.hex if data.base_task_id else None
        client_id = data.client_task_id.hex
        request_json = encode({"project_id": project_id, "instruction": data.instruction, "base_task_id": base_id})
        with self.auth.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            project = self._owned_project(db, user_id, project_id)
            existing = db.execute("SELECT * FROM coding_tasks WHERE user_id = ? AND client_task_id = ?", (user_id, client_id)).fetchone()
            if existing:
                if existing["request_json"] != request_json:
                    raise CodingError("同一任务请求编号不能重复用于不同内容", "CODING_REQUEST_CONFLICT", 409)
                return self._task(existing), True
            if db.execute("SELECT 1 FROM coding_tasks WHERE user_id = ? AND status IN ('queued','running')", (user_id,)).fetchone():
                raise CodingError("你已有编程任务运行中，请等待完成或取消", "CODING_USER_BUSY", 409)
            if db.execute("SELECT count(*) FROM coding_tasks WHERE status IN ('queued','running')").fetchone()[0] >= global_limit:
                raise CodingError("编程任务服务正忙，请稍后再试", "CODING_SERVICE_BUSY", 429)
            if db.execute("SELECT count(*) FROM coding_tasks WHERE user_id = ?", (user_id,)).fetchone()[0] >= MAX_TASKS_PER_USER:
                raise CodingError("最多保留 100 个任务，请下载成果后删除不再使用的项目", "CODING_TASK_LIMIT", 409)
            baseline = json.loads(project["source_files_json"])
            if base_id:
                base = self._owned_task(db, user_id, base_id)
                if base["project_id"] != project_id or base["status"] not in TERMINAL_STATUSES:
                    raise CodingError("只能从同一项目已结束的任务继续修改", "CODING_BASE_CONFLICT", 409)
                baseline = json.loads(base["files_json"])
            validate_files(baseline)
            # Counter survives project/task deletion, avoiding daily quota bypass.
            bucket = f"coding:daily:{user_id}"
            quota = db.execute("SELECT count,reset_at FROM auth_attempts WHERE bucket = ?", (bucket,)).fetchone()
            used = quota["count"] if quota and quota["reset_at"] > now else 0
            if used >= daily_limit:
                raise CodingError("今日编程任务额度已用完，请于 UTC 次日再试", "CODING_DAILY_LIMIT", 429)
            reset_at = (now // 86400 + 1) * 86400
            db.execute("INSERT INTO auth_attempts(bucket,count,reset_at) VALUES(?,?,?) ON CONFLICT(bucket) DO UPDATE SET count=excluded.count,reset_at=excluded.reset_at", (bucket, used + 1, reset_at))
            source = encode(baseline)
            db.execute("""INSERT INTO coding_tasks
                (id,user_id,project_id,client_task_id,base_task_id,instruction,request_json,status,
                 baseline_files_json,files_json,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,'queued',?,?,?,?)""",
                (identifier, user_id, project_id, client_id, base_id, data.instruction, request_json, source, source, now, now))
            db.execute("UPDATE coding_projects SET updated_at = ? WHERE id = ? AND user_id = ?", (now, project_id, user_id))
            return self._task(self._owned_task(db, user_id, identifier)), False

    def get_task(self, user_id, identifier):
        with self.auth.connect() as db:
            return self._task(self._owned_task(db, user_id, identifier))

    def save_task(self, user_id, identifier, *, files=None, steps=None, checks=None, status=None, summary=None, error=None):
        """Replace supplied snapshots. Terminal rows are immutable; return current row."""
        updates = {}
        if files is not None:
            validate_files(files)
            updates["files_json"] = encode(files)
        for name, value, max_items in (("steps", steps, 400), ("checks", checks, 13)):
            if value is not None:
                if not isinstance(value, list) or len(value) > max_items:
                    raise CodingError("任务事件超出保存范围", "CODING_EVENT_LIMIT")
                rendered = encode(value)
                if len(rendered.encode("utf-8")) > 1024 * 1024:
                    raise CodingError("任务事件内容过长", "CODING_EVENT_LIMIT")
                updates[f"{name}_json"] = rendered
        if summary is not None:
            updates["summary"] = str(summary)[:12_000]
        if error is not None:
            updates["error"] = str(error)[:2000]
        if status is not None:
            if status not in ACTIVE_STATUSES | TERMINAL_STATUSES:
                raise CodingError("无效的编程任务状态", "CODING_STATUS_INVALID")
            updates["status"] = status
        with self.auth.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            current = self._owned_task(db, user_id, identifier)
            if current["status"] in TERMINAL_STATUSES:
                return self._task(current)
            if current["status"] == "running" and status == "queued":
                raise CodingError("运行中的任务不能重新入队", "CODING_STATUS_CONFLICT", 409)
            if current["cancel_requested"] and status == "completed":
                updates["status"] = "cancelled"
            if updates:
                updates["updated_at"] = int(time.time())
                db.execute("UPDATE coding_tasks SET " + ",".join(f"{key} = ?" for key in updates) + " WHERE id = ? AND user_id = ?", (*updates.values(), identifier, user_id))
            return self._task(self._owned_task(db, user_id, identifier))

    def request_cancel(self, user_id, identifier):
        with self.auth.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            task = self._owned_task(db, user_id, identifier)
            if task["status"] in ACTIVE_STATUSES:
                db.execute("UPDATE coding_tasks SET cancel_requested=1,updated_at=? WHERE id=? AND user_id=?", (int(time.time()), identifier, user_id))
            return self._task(self._owned_task(db, user_id, identifier))

    def is_cancelled(self, user_id, identifier):
        with self.auth.connect() as db:
            row = self._owned_task(db, user_id, identifier)
            return bool(row["cancel_requested"]) or row["status"] in {"cancelled", "interrupted"}

    def recover_interrupted(self):
        """Call once on startup, before scheduling jobs (single-process MVP)."""
        with self.auth.connect() as db:
            cursor = db.execute("""UPDATE coding_tasks SET status='interrupted',cancel_requested=1,
                error='服务重启导致任务中断，可基于已保存的结果新建任务继续。',updated_at=?
                WHERE status IN ('queued','running')""", (int(time.time()),))
            return cursor.rowcount
