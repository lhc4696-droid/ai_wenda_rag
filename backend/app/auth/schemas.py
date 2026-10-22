from typing import Literal
import unicodedata

from email_validator import EmailNotValidError, validate_email
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


class Credentials(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=1, max_length=32)
    password: SecretStr = Field(min_length=8, max_length=128)

    @field_validator("username", mode="before")
    @classmethod
    def strip_username(cls, value):
        if not isinstance(value, str):
            return value
        # Check before trimming so leading/trailing control characters are not
        # silently accepted. Joiners support composed emoji and natural scripts.
        joiners = "\u200c\u200d"
        if any(not (unicodedata.category(char)[0] in "LMNPS"
                    or unicodedata.category(char) == "Zs" or char in joiners)
               for char in value):
            raise ValueError("用户名不能包含换行、控制字符或不可见格式字符")
        value = value.strip()
        # These blank glyphs are classified as letters/symbols, not whitespace.
        blank_glyphs = "\u115f\u1160\u3164\uffa0\u2800"
        if (not value or value[0] in joiners or value[-1] in joiners
                or not any(unicodedata.category(char)[0] in "LNPS" and char not in blank_glyphs
                           for char in value)):
            raise ValueError("用户名必须包含可见字符，不能全部为空白或不可见字符")
        return value

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
    username: str = Field(min_length=1, max_length=32)

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
