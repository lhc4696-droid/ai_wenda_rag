from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=20_000)

    @field_validator("content")
    @classmethod
    def content_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("消息内容不能为空")
        return value


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def latest_message_must_be_from_user(self) -> "ChatRequest":
        if self.messages[-1].role != "user":
            raise ValueError("最后一条消息必须来自用户")
        return self


class ChatResponse(BaseModel):
    role: Literal["assistant"] = "assistant"
    content: str = Field(min_length=1, max_length=20_000)
    model: str
    context_trimmed: bool = False
    answer_truncated: bool = False
    request_id: str | None = None


class ChatUsageResponse(BaseModel):
    requests: int
    succeeded: int
    input_tokens: int | None
    output_tokens: int | None
    daily_limit: int
    resets_at: int


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"


class ErrorResponse(BaseModel):
    detail: str
