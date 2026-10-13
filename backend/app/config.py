from dataclasses import dataclass, field
from functools import lru_cache
import os
from pathlib import Path

from dotenv import load_dotenv

from .integrations.endpoint_policy import normalize_proxy_settings
from .config_groups import (
    SMTPConfig, EmailCodeConfig, ChatConfig, AttachmentConfig,
    RagConfig, CodingConfig, CustomModelConfig
)


BACKEND_DIR = Path(__file__).resolve().parents[1]
load_dotenv(BACKEND_DIR / ".env")


@dataclass(frozen=True)
class Settings:
    """应用主配置，聚合各功能模块配置"""
    # 核心配置
    dashscope_api_key: str | None = field(repr=False)
    qwen_model: str
    qwen_vision_model: str
    qwen_coding_model: str
    system_prompt: str
    cors_origins: tuple[str, ...]

    # 认证配置
    auth_db_path: Path = BACKEND_DIR / "data" / "zhiyan.sqlite3"
    session_max_age: int = 604800
    session_cookie_secure: bool = False
    model_config_secret: str = field(default="", repr=False)

    # 功能模块配置（分组）
    smtp: SMTPConfig = field(default_factory=SMTPConfig)
    email_code: EmailCodeConfig = field(default_factory=EmailCodeConfig)
    chat: ChatConfig = field(default_factory=ChatConfig)
    attachment: AttachmentConfig = field(default_factory=AttachmentConfig)
    rag: RagConfig = field(default_factory=RagConfig)
    coding: CodingConfig = field(default_factory=CodingConfig)
    custom_model: CustomModelConfig = field(default_factory=CustomModelConfig)

    # 向后兼容的属性访问（避免一次性修改所有代码）
    @property
    def smtp_host(self) -> str:
        return self.smtp.host

    @property
    def smtp_port(self) -> int:
        return self.smtp.port

    @property
    def smtp_username(self) -> str:
        return self.smtp.username

    @property
    def smtp_password(self) -> str:
        return self.smtp.password

    @property
    def smtp_from(self) -> str:
        return self.smtp.from_address

    @property
    def smtp_security(self) -> str:
        return self.smtp.security

    @property
    def smtp_timeout(self) -> int:
        return self.smtp.timeout

    @property
    def email_code_secret(self) -> str:
        return self.email_code.secret

    @property
    def email_code_ttl(self) -> int:
        return self.email_code.ttl

    @property
    def email_code_cooldown(self) -> int:
        return self.email_code.cooldown

    @property
    def email_code_max_attempts(self) -> int:
        return self.email_code.max_attempts

    @property
    def chat_timeout(self) -> int:
        return self.chat.timeout

    @property
    def chat_context_chars(self) -> int:
        return self.chat.context_chars

    @property
    def chat_max_output_tokens(self) -> int:
        return self.chat.max_output_tokens

    @property
    def chat_requests_per_minute(self) -> int:
        return self.chat.requests_per_minute

    @property
    def chat_requests_per_day(self) -> int:
        return self.chat.requests_per_day

    @property
    def chat_global_requests_per_day(self) -> int:
        return self.chat.global_requests_per_day

    @property
    def chat_user_concurrency(self) -> int:
        return self.chat.user_concurrency

    @property
    def chat_global_concurrency(self) -> int:
        return self.chat.global_concurrency

    @property
    def attachment_max_bytes(self) -> int:
        return self.attachment.max_bytes

    @property
    def attachment_user_quota(self) -> int:
        return self.attachment.user_quota

    @property
    def rag_enabled(self) -> bool:
        return self.rag.enabled

    @property
    def qdrant_url(self) -> str:
        return self.rag.qdrant_url

    @property
    def qdrant_collection(self) -> str:
        return self.rag.qdrant_collection

    @property
    def embedding_model(self) -> str:
        return self.rag.embedding_model

    @property
    def embedding_dimension(self) -> int:
        return self.rag.embedding_dimension

    @property
    def coding_max_steps(self) -> int:
        return self.coding.max_steps

    @property
    def coding_task_timeout(self) -> int:
        return self.coding.task_timeout

    @property
    def coding_tasks_per_day(self) -> int:
        return self.coding.tasks_per_day

    @property
    def coding_global_concurrency(self) -> int:
        return self.coding.global_concurrency

    @property
    def coding_sandbox_enabled(self) -> bool:
        return self.coding.sandbox_enabled

    @property
    def coding_sandbox_image(self) -> str:
        return self.coding.sandbox_image

    @property
    def coding_check_timeout(self) -> int:
        return self.coding.check_timeout

    @property
    def custom_model_proxy_url(self) -> str:
        return self.custom_model.proxy_url

    @property
    def custom_model_proxy_base_urls(self) -> tuple[str, ...]:
        return self.custom_model.proxy_base_urls

    def __post_init__(self):
        """验证配置，规范化代理设置"""
        # 规范化代理配置
        proxy, bases = normalize_proxy_settings(self.custom_model.proxy_url, self.custom_model.proxy_base_urls)
        object.__setattr__(self, "custom_model", CustomModelConfig(proxy_url=proxy, proxy_base_urls=bases))

        # 验证各模块配置
        self.chat.validate(len(self.system_prompt))
        self.attachment.validate()
        self.rag.validate()
        self.coding.validate()


def env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        # 只显示配置名，不在异常里回显用户误填的敏感值。
        raise ValueError(f"{name} 必须填写整数") from None


@lru_cache
def get_settings() -> Settings:
    """从环境变量构建应用配置，使用lru_cache确保单例"""
    origins = os.getenv(
        "CORS_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173",
    )
    db_path = Path(os.getenv("AUTH_DB_PATH", "data/zhiyan.sqlite3"))
    if not db_path.is_absolute():
        db_path = BACKEND_DIR / db_path
    session_max_age = env_int("SESSION_MAX_AGE", 604800)
    if session_max_age < 1:
        raise ValueError("SESSION_MAX_AGE 必须大于 0")

    # 构建SMTP配置
    smtp = SMTPConfig(
        host=os.getenv("SMTP_HOST", "").strip(),
        port=env_int("SMTP_PORT", 465),
        username=os.getenv("SMTP_USERNAME", "").strip(),
        password=os.getenv("SMTP_PASSWORD", ""),
        from_address=os.getenv("SMTP_FROM", "").strip(),
        security=os.getenv("SMTP_SECURITY", "ssl").lower(),
        timeout=env_int("SMTP_TIMEOUT", 10),
    )

    # 构建邮箱验证码配置
    email_code = EmailCodeConfig(
        secret=os.getenv("EMAIL_CODE_SECRET", ""),
        ttl=env_int("EMAIL_CODE_TTL", 300),
        cooldown=env_int("EMAIL_CODE_COOLDOWN", 60),
        max_attempts=env_int("EMAIL_CODE_MAX_ATTEMPTS", 5),
    )

    # 构建聊天配置
    chat = ChatConfig(
        timeout=env_int("CHAT_TIMEOUT", 90),
        context_chars=env_int("CHAT_CONTEXT_CHARS", 40_000),
        max_output_tokens=env_int("CHAT_MAX_OUTPUT_TOKENS", 2048),
        requests_per_minute=env_int("CHAT_REQUESTS_PER_MINUTE", 10),
        requests_per_day=env_int("CHAT_REQUESTS_PER_DAY", 100),
        global_requests_per_day=env_int("CHAT_GLOBAL_REQUESTS_PER_DAY", 1000),
        user_concurrency=env_int("CHAT_USER_CONCURRENCY", 1),
        global_concurrency=env_int("CHAT_GLOBAL_CONCURRENCY", 4),
    )

    # 构建附件配置
    attachment = AttachmentConfig(
        max_bytes=env_int("ATTACHMENT_MAX_BYTES", 10 * 1024 * 1024),
        user_quota=env_int("ATTACHMENT_USER_QUOTA", 200 * 1024 * 1024),
    )

    # 构建RAG配置
    rag = RagConfig(
        enabled=os.getenv("RAG_ENABLED", "true").lower() == "true",
        qdrant_url=os.getenv("QDRANT_URL", "http://127.0.0.1:6333").rstrip("/"),
        qdrant_collection=os.getenv("QDRANT_COLLECTION", "zhiyan_documents_v1").strip(),
        embedding_model=os.getenv("EMBEDDING_MODEL", "text-embedding-v4").strip(),
        embedding_dimension=env_int("EMBEDDING_DIMENSION", 1024),
    )

    # 构建Coding配置
    coding = CodingConfig(
        max_steps=env_int("CODING_MAX_STEPS", 12),
        task_timeout=env_int("CODING_TASK_TIMEOUT", 300),
        tasks_per_day=env_int("CODING_TASKS_PER_DAY", 20),
        global_concurrency=env_int("CODING_GLOBAL_CONCURRENCY", 4),
        sandbox_enabled=os.getenv("CODING_SANDBOX_ENABLED", "false").lower() == "true",
        sandbox_image=os.getenv("CODING_SANDBOX_IMAGE", "zhiyan-coding-python:1").strip(),
        check_timeout=env_int("CODING_CHECK_TIMEOUT", 45),
    )

    # 构建自定义模型配置
    custom_model = CustomModelConfig(
        proxy_url=os.getenv("CUSTOM_MODEL_PROXY_URL", ""),
        proxy_base_urls=tuple(os.getenv("CUSTOM_MODEL_PROXY_BASE_URLS", "").split(","))
            if os.getenv("CUSTOM_MODEL_PROXY_BASE_URLS", "").strip() else (),
    )

    return Settings(
        dashscope_api_key=os.getenv("DASHSCOPE_API_KEY"),
        qwen_model=os.getenv("QWEN_MODEL", "qwen-plus"),
        qwen_vision_model=os.getenv("QWEN_VISION_MODEL", "").strip(),
        qwen_coding_model=os.getenv("QWEN_CODING_MODEL", "").strip(),
        system_prompt=os.getenv(
            "SYSTEM_PROMPT",
            "你是一个友好、准确的中文 AI 助手。回答应清晰、简洁；不确定时应明确说明。",
        ),
        cors_origins=tuple(origin.strip() for origin in origins.split(",") if origin.strip()),
        auth_db_path=db_path,
        session_max_age=session_max_age,
        session_cookie_secure=os.getenv("SESSION_COOKIE_SECURE", "false").lower() == "true",
        model_config_secret=os.getenv("MODEL_CONFIG_SECRET", ""),
        smtp=smtp,
        email_code=email_code,
        chat=chat,
        attachment=attachment,
        rag=rag,
        coding=coding,
        custom_model=custom_model,
    )
