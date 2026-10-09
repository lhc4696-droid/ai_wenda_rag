from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator
from app.attachments.schemas import AttachmentResponse


class CreateConversation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # 客户端生成 ID，使创建响应丢失后的重试仍指向同一会话。
    id: UUID = Field(default_factory=uuid4)


class RenameConversation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=80)

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value):
        value = " ".join(value.split())
        if not value:
            raise ValueError("会话标题不能为空")
        return value


class SendMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str = Field(min_length=1, max_length=20_000)
    client_message_id: UUID
    expected_last_sequence: int = Field(ge=0, le=2_147_483_647)
    retry: bool = False
    attachment_ids: list[UUID] = Field(default_factory=list, max_length=3)
    source_mode: Literal["legacy", "chat", "documents"] = "legacy"

    @field_validator("attachment_ids")
    @classmethod
    def unique_attachments(cls, value):
        if len(set(value)) != len(value):
            raise ValueError("附件编号不能重复")
        return value

    @field_validator("content")
    @classmethod
    def nonblank(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("问题不能为空")
        return value


class ConversationResponse(BaseModel):
    id: str
    title: str
    created_at: int
    updated_at: int
    last_sequence: int


class MessageResponse(BaseModel):
    id: str
    client_message_id: str
    sequence: int
    role: Literal["user", "assistant"]
    content: str
    status: Literal["pending", "completed", "failed"]
    model: str | None
    request_id: str
    error_code: str | None
    error_message: str | None
    context_trimmed: bool
    answer_truncated: bool
    created_at: int
    attachments: list[AttachmentResponse] = Field(default_factory=list)
    source_mode: Literal["legacy", "chat", "documents"] = "legacy"
    sources: list[dict] = Field(default_factory=list)


class ConversationPage(BaseModel):
    items: list[ConversationResponse]
    next_cursor: str | None


class MessagePage(BaseModel):
    conversation: ConversationResponse
    messages: list[MessageResponse]
    next_before: int | None


class TurnResponse(BaseModel):
    conversation: ConversationResponse
    user_message: MessageResponse
    assistant_message: MessageResponse
    replayed: bool = False
