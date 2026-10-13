"""Browser-executed tools. Neither a path nor a client claim grants server disk access."""

import json
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .schemas import CreateTask, TaskStep
from .workspace import validate_path


class LocalCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    instruction: str = Field(min_length=1, max_length=6000)
    directory_name: str = Field(min_length=1, max_length=120)
    conversation_id: UUID
    client_task_id: UUID
    grant_token: str = Field(min_length=32, max_length=128, pattern=r"^[A-Za-z0-9_-]+$", repr=False)
    paths: list[str] = Field(default_factory=list, max_length=150)

    @field_validator("instruction", "directory_name")
    @classmethod
    def text(cls, value):
        return CreateTask.nonblank(value)

    @field_validator("paths")
    @classmethod
    def paths_valid(cls, values):
        if len({p.casefold() for p in values}) != len(values):
            raise ValueError("文件路径重复")
        for path in values:
            try:
                validate_path(path)
            except Exception:
                raise ValueError("文件路径无效") from None
        return values


class LocalResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool_call_id: str = Field(min_length=1, max_length=200)
    result: str = Field(max_length=20000)

    @field_validator("result")
    @classmethod
    def object_json(cls, value):
        try:
            value.encode("utf-8")
            data = json.loads(value)
            if not isinstance(data, dict) or not isinstance(data.get("ok"), bool):
                raise ValueError()
        except (ValueError, UnicodeError, RecursionError):
            raise ValueError("工具结果必须是包含 ok 布尔值的 JSON 对象") from None
        return value


class LocalResults(BaseModel):
    model_config = ConfigDict(extra="forbid")
    grant_token: str = Field(min_length=32, max_length=128, pattern=r"^[A-Za-z0-9_-]+$", repr=False)
    round: int = Field(ge=1, le=30)
    results: list[LocalResult] = Field(min_length=1, max_length=4)


class LocalTaskResponse(BaseModel):
    id: str
    conversation_id: str
    turn_index: int
    client_task_id: str
    directory_name: str
    instruction: str
    status: Literal["queued", "running", "waiting_tools", "completed", "failed", "cancelled", "interrupted"]
    round: int
    pending_tools: list[dict]
    steps: list[TaskStep]
    summary: str
    error: str
    created_at: int
    updated_at: int
    deadline_at: int


class LocalCapabilities(BaseModel):
    model: str
    max_steps: int


class LocalTaskList(BaseModel):
    items: list[LocalTaskResponse]


class LocalProjectName(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(default="", max_length=120)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value):
        value = value.strip()
        if any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("项目名称不能包含控制字符或换行")
        if value:
            return CreateTask.nonblank(value)
        return ""


class LocalProjectRename(LocalProjectName):
    name: str = Field(max_length=120)


class LocalProjectCreate(LocalProjectName):
    client_project_id: UUID
    directory_name: str = Field(min_length=1, max_length=120)

    @field_validator("directory_name")
    @classmethod
    def nonblank(cls, value):
        return CreateTask.nonblank(value)


class LocalProjectResponse(BaseModel):
    id: str
    name: str
    directory_name: str
    created_at: int
    updated_at: int


class LocalProjectList(BaseModel):
    items: list[LocalProjectResponse]


class LocalConversationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_conversation_id: UUID
    # Optional only for older clients: they receive a separate project automatically.
    project_id: UUID | None = None
    directory_name: str = Field(min_length=1, max_length=120)

    @field_validator("directory_name")
    @classmethod
    def nonblank(cls, value):
        return CreateTask.nonblank(value)


class LocalConversationResponse(BaseModel):
    id: str
    project_id: str
    directory_name: str
    title: str
    created_at: int
    updated_at: int
    turn_count: int


class LocalConversationDetail(LocalConversationResponse):
    tasks: list[LocalTaskResponse]


class LocalConversationList(BaseModel):
    items: list[LocalConversationResponse]
