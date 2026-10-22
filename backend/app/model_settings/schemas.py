from dataclasses import dataclass, field
import unicodedata
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator


class ModelSettingsError(RuntimeError):
    def __init__(self, message: str, *, status: int = 400, code: str = "MODEL_SETTINGS_INVALID"):
        super().__init__(message)
        self.status = status
        self.code = code


@dataclass(frozen=True)
class ModelProfile:
    base_url: str
    api_key: str = field(repr=False)
    chat_model: str
    coding_model: str
    vision_model: str = ""
    max_output_tokens: int | None = None  # None 表示使用全局默认值


class ModelSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    enabled: bool | None = None
    base_url: str | None = Field(default=None, max_length=2048)
    api_key: SecretStr | None = Field(default=None, max_length=2048)
    chat_model: str | None = Field(default=None, max_length=200)
    coding_model: str | None = Field(default=None, max_length=200)
    vision_model: str | None = Field(default=None, max_length=200)
    max_output_tokens: int | None = Field(default=None, ge=1, le=32768)

    @model_validator(mode="before")
    @classmethod
    def explicit_null_is_not_a_patch(cls, value):
        if isinstance(value, dict) and any(item is None for item in value.values()):
            raise ValueError("不修改的字段请省略，不要填写 null")
        return value

    @field_validator("chat_model", "coding_model", "vision_model")
    @classmethod
    def clean_model(cls, value):
        if value is None:
            return value
        if any(unicodedata.category(char) in {"Cc", "Cf", "Cs"} for char in value):
            raise ValueError("模型名称不能包含控制字符")
        return value.strip()

    @field_validator("api_key")
    @classmethod
    def clean_key(cls, value):
        if value is not None and any(not 33 <= ord(char) <= 126 for char in value.get_secret_value()):
            raise ValueError("API Key 不能包含空格、换行或非 ASCII 字符")
        return value


class ModelSettingsResponse(BaseModel):
    enabled: bool
    base_url: str
    chat_model: str
    coding_model: str
    vision_model: str
    max_output_tokens: int | None
    has_api_key: bool
    storage_ready: bool
    default_chat_model: str
    default_coding_model: str
    default_vision_model: str
    default_max_output_tokens: int
    updated_at: int | None


class ModelConnectionTestRequest(BaseModel):
    """A draft, not a settings mutation; accepts no history, tools or user ID."""

    model_config = ConfigDict(extra="forbid", strict=True)

    target: Literal["chat", "coding"]
    base_url: str = Field(min_length=1, max_length=2048)
    chat_model: str = Field(min_length=1, max_length=200)
    coding_model: str = Field(default="", max_length=200)
    api_key: SecretStr | None = Field(default=None, max_length=2048)

    @model_validator(mode="before")
    @classmethod
    def reject_explicit_null(cls, value):
        return ModelSettingsUpdate.explicit_null_is_not_a_patch(value)

    @field_validator("chat_model", "coding_model")
    @classmethod
    def clean_model(cls, value):
        return ModelSettingsUpdate.clean_model(value)

    @field_validator("api_key")
    @classmethod
    def clean_key(cls, value):
        return ModelSettingsUpdate.clean_key(value)

    @model_validator(mode="after")
    def require_chat_model(self):
        if not self.chat_model:
            raise ValueError("请填写聊天模型名称")
        return self


class ModelConnectionTestResponse(BaseModel):
    ok: bool
    target: Literal["chat", "coding"]
    model: str
    elapsed_ms: int = Field(ge=0)
    message: str
    error_code: str | None
    input_tokens: int | None
    output_tokens: int | None
    request_id: str
