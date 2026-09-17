from typing import Literal

from email_validator import EmailNotValidError, validate_email
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


class Credentials(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=3, max_length=32, pattern=r"^[A-Za-z0-9_]+$")
    password: SecretStr = Field(min_length=8, max_length=128)

    @field_validator("username", mode="before")
    @classmethod
    def strip_username(cls, value):
        return value.strip() if isinstance(value, str) else value

    @field_validator("password")
    @classmethod
    def password_not_blank(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("密码不能全部为空格")
        return value


class UserResponse(BaseModel):
    id: int
    username: str
    email: str | None = None


class AuthResponse(BaseModel):
    user: UserResponse


def normalize_email(value: str) -> str:
    """保留邮箱本地部分，规范化域名；不擅自合并点号或 + 别名。"""
    try:
        return validate_email(value.strip(), check_deliverability=False, allow_smtputf8=False).normalized
    except EmailNotValidError:
        raise ValueError("请输入有效的邮箱地址") from None


class EmailRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=3, max_length=254)
    _email = field_validator("email")(normalize_email)


class SendEmailCodeRequest(EmailRequest):
    purpose: Literal["register", "reset_password"]


class CodeRequest(EmailRequest):
    code: SecretStr = Field(min_length=6, max_length=6)

    @field_validator("code")
    @classmethod
    def numeric_code(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().isascii() or not value.get_secret_value().isdigit():
            raise ValueError("验证码应为 6 位数字")
        return value


class RegisterRequest(Credentials, CodeRequest):
    """只有注册需要邮箱验证码，登录继续使用 Credentials。"""


class ResetPasswordRequest(CodeRequest):
    new_password: SecretStr = Field(min_length=8, max_length=128)

    @field_validator("new_password")
    @classmethod
    def password_not_blank(cls, value: SecretStr) -> SecretStr:
        return Credentials.password_not_blank(value)


class MessageResponse(BaseModel):
    message: str


class EmailCodeResponse(MessageResponse):
    retry_after: int
    expires_in: int


class CurrentPasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    current_password: SecretStr = Field(min_length=8, max_length=128)

    @field_validator("current_password")
    @classmethod
    def current_password_not_blank(cls, value):
        return Credentials.password_not_blank(value)


class RenameAccountRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=3, max_length=32, pattern=r"^[A-Za-z0-9_]+$")

    @field_validator("username", mode="before")
    @classmethod
    def strip_username(cls, value):
        return Credentials.strip_username(value)


class ChangePasswordRequest(CurrentPasswordRequest):
    new_password: SecretStr = Field(min_length=8, max_length=128)

    @field_validator("new_password")
    @classmethod
    def new_password_not_blank(cls, value):
        return Credentials.password_not_blank(value)
