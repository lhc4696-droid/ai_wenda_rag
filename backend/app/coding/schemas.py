"""Public Coding Agent contracts; source snapshots stay server-side."""

import difflib
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class CodingError(Exception):
    def __init__(self, message: str, code="CODING_INVALID", status=400):
        super().__init__(message)
        self.code, self.status = code, status


class CreateTask(BaseModel):
    model_config = ConfigDict(extra="forbid")
    instruction: str = Field(min_length=1, max_length=6000)
    client_task_id: UUID
    base_task_id: UUID | None = None

    @field_validator("instruction")
    @classmethod
    def nonblank(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("任务要求不能为空")
        try:
            value.encode("utf-8")
        except UnicodeError as error:
            raise ValueError("任务要求包含无效 Unicode 字符") from error
        return value


TaskStatus = Literal["queued", "running", "completed", "failed", "cancelled", "interrupted"]
ACTIVE_STATUSES = frozenset({"queued", "running"})
TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled", "interrupted"})


class CodingCapabilities(BaseModel):
    model: str
    sandbox_enabled: bool
    max_upload_bytes: int
    max_files: int
    max_file_bytes: int
    max_total_bytes: int
    max_steps: int


class SourceFile(BaseModel):
    path: str
    size: int


class ProjectResponse(BaseModel):
    id: str
    name: str
    created_at: int
    updated_at: int
    warnings: list[str]
    files: list[SourceFile]
    file_count: int
    total_bytes: int


class ProjectList(BaseModel):
    items: list[ProjectResponse]


class TaskSummary(BaseModel):
    id: str
    project_id: str
    status: TaskStatus
    instruction: str
    summary: str
    error: str
    created_at: int
    updated_at: int
    base_task_id: str | None
    cancel_requested: bool


class TaskStep(BaseModel):
    sequence: int
    kind: str
    tool: str
    message: str


class TaskChange(BaseModel):
    path: str
    status: Literal["added", "modified", "deleted"]
    diff: str
    diff_truncated: bool


class TaskCheck(BaseModel):
    kind: Literal["syntax", "pytest"]
    status: Literal["passed", "failed", "unavailable"]
    exit_code: int | None
    output: str
    revision: int
    stale: bool


class TaskResponse(TaskSummary):
    steps: list[TaskStep]
    changes: list[TaskChange]
    checks: list[TaskCheck]
    files: list[SourceFile]


class TaskList(BaseModel):
    items: list[TaskSummary]


class FileContent(BaseModel):
    path: str
    content: str


def file_list(files):
    return [{"path": path, "size": len(text.encode("utf-8"))} for path, text in sorted(files.items())]


def public_project(project):
    return {key: project[key] for key in ("id", "name", "created_at", "updated_at", "warnings")} | {
        "files": file_list(project["source_files"]),
        "file_count": len(project["source_files"]),
        "total_bytes": sum(len(text.encode("utf-8")) for text in project["source_files"].values()),
    }


def task_changes(task):
    """Bound response size while retaining every changed path."""
    previous, current = task["baseline_files"], task["files"]
    result, remaining = [], 160_000
    for path in sorted(previous.keys() | current.keys()):
        before, after = previous.get(path), current.get(path)
        if before == after:
            continue
        status = "added" if before is None else "deleted" if after is None else "modified"
        if remaining <= 0 or len(before or "") + len(after or "") > 100_000 or (before or "").count("\n") + (after or "").count("\n") > 6000:
            result.append({"path": path, "status": status, "diff": "", "diff_truncated": True})
            continue
        lines = difflib.unified_diff((before or "").splitlines(keepends=True),
                                     (after or "").splitlines(keepends=True),
                                     fromfile=f"a/{path}", tofile=f"b/{path}")
        parts, used, truncated = [], 0, False
        allowance = min(20_000, remaining)
        for line in lines:
            if not line.endswith(("\n", "\r")):
                line += "\n\\ No newline at end of file\n"
            if used + len(line) > allowance:
                truncated = True
                break
            parts.append(line)
            used += len(line)
        remaining -= used
        result.append({"path": path, "status": status, "diff": "".join(parts), "diff_truncated": truncated})
    return result


def public_task(task, *, detail=True):
    result = {key: task[key] for key in (
        "id", "project_id", "status", "instruction", "summary", "error", "created_at", "updated_at", "base_task_id",
    )}
    result["cancel_requested"] = bool(task["cancel_requested"])
    if detail:
        result.update(steps=task["steps"], checks=task["checks"], changes=task_changes(task), files=file_list(task["files"]))
    return result
