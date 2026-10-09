from dataclasses import dataclass, field
from functools import lru_cache
import os
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv


BACKEND_DIR = Path(__file__).resolve().parents[1]
load_dotenv(BACKEND_DIR / ".env")


@dataclass(frozen=True)
class Settings:
    dashscope_api_key: str | None = field(repr=False)
    qwen_model: str
    system_prompt: str
    cors_origins: tuple[str, ...]
    auth_db_path: Path = BACKEND_DIR / "data" / "zhiyan.sqlite3"
    session_max_age: int = 604800
    session_cookie_secure: bool = False
    smtp_host: str = ""
    smtp_port: int = 465
    smtp_username: str = ""
    smtp_password: str = field(default="", repr=False)
    smtp_from: str = ""
    smtp_security: str = "ssl"
    smtp_timeout: int = 10
    email_code_secret: str = field(default="", repr=False)
    email_code_ttl: int = 300
    email_code_cooldown: int = 60
    email_code_max_attempts: int = 5
    chat_timeout: int = 90
    chat_context_chars: int = 40_000
    chat_max_output_tokens: int = 2048
    chat_requests_per_minute: int = 10
    chat_requests_per_day: int = 100
    chat_global_requests_per_day: int = 1000
    chat_user_concurrency: int = 1
    chat_global_concurrency: int = 4
    qwen_vision_model: str = ""
    attachment_max_bytes: int = 10 * 1024 * 1024
    attachment_user_quota: int = 200 * 1024 * 1024
    rag_enabled: bool = False
    qdrant_url: str = "http://127.0.0.1:6333"
    qdrant_collection: str = "zhiyan_documents_v1"
    embedding_model: str = "text-embedding-v4"
    embedding_dimension: int = 1024
    qwen_coding_model: str = ""
    coding_max_steps: int = 12
    coding_task_timeout: int = 300
    coding_tasks_per_day: int = 20
    coding_global_concurrency: int = 4
    coding_sandbox_enabled: bool = False
    coding_sandbox_image: str = "zhiyan-coding-python:1"
    coding_check_timeout: int = 45

    def __post_init__(self):
        if not 5 <= self.chat_timeout <= 100:
            raise ValueError("CHAT_TIMEOUT 必须为 5–100 秒，低于前端 120 秒等待上限")
        if not 20_000 <= self.chat_context_chars <= 200_000:
            raise ValueError("CHAT_CONTEXT_CHARS 必须为 20000–200000")
        if len(self.system_prompt) >= self.chat_context_chars:
            raise ValueError("SYSTEM_PROMPT 超出 CHAT_CONTEXT_CHARS 预算")
        if not 1 <= self.chat_max_output_tokens <= 8192:
            raise ValueError("CHAT_MAX_OUTPUT_TOKENS 必须为 1–8192")
        for name in ("chat_requests_per_minute", "chat_requests_per_day", "chat_global_requests_per_day", "chat_user_concurrency", "chat_global_concurrency"):
            if getattr(self, name) < 1:
                raise ValueError(f"{name.upper()} 必须大于 0")
        if not 1 <= self.attachment_max_bytes <= 10 * 1024 * 1024:
            raise ValueError("ATTACHMENT_MAX_BYTES 必须在 1–10485760 之间")
        if self.attachment_user_quota < self.attachment_max_bytes + 3 * 1024 * 1024:
            raise ValueError("ATTACHMENT_USER_QUOTA 至少为单个附件上限加 3145728 字节预览预留空间")
        if not self.embedding_model:
            raise ValueError("EMBEDDING_MODEL 不能为空")
        if not 64 <= self.embedding_dimension <= 4096:
            raise ValueError("EMBEDDING_DIMENSION 必须在 64–4096 之间，并与模型实际输出一致")
        try:
            qdrant = urlsplit(self.qdrant_url)
            local_qdrant = (qdrant.scheme == "http" and qdrant.hostname in {"127.0.0.1", "localhost"}
                             and qdrant.port is not None and not qdrant.username and not qdrant.password
                             and not qdrant.path and not qdrant.query and not qdrant.fragment)
        except ValueError:
            local_qdrant = False
        if not local_qdrant:
            raise ValueError("本地开发的 QDRANT_URL 必须是本机 HTTP 地址，例如 http://127.0.0.1:6333")
        if not self.qdrant_collection.replace("_", "").replace("-", "").isalnum():
            raise ValueError("QDRANT_COLLECTION 只能包含字母、数字、下划线和连字符")
        if not 1 <= self.coding_max_steps <= 30:
            raise ValueError("CODING_MAX_STEPS 必须在 1–30 之间")
        if not 60 <= self.coding_task_timeout <= 900:
            raise ValueError("CODING_TASK_TIMEOUT 必须在 60–900 秒之间")
        if not 5 <= self.coding_check_timeout <= 120:
            raise ValueError("CODING_CHECK_TIMEOUT 必须在 5–120 秒之间")
        if not 1 <= self.coding_tasks_per_day <= 100 or not 1 <= self.coding_global_concurrency <= 16:
            raise ValueError("Coding Agent 每日任务上限为 1–100，并发上限为 1–16")
        if not self.coding_sandbox_image or self.coding_sandbox_image.startswith("-") or any(c.isspace() for c in self.coding_sandbox_image):
            raise ValueError("CODING_SANDBOX_IMAGE 必须为有效的本地镜像名称")


def env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        # 只显示配置名，不在异常里回显用户误填的敏感值。
        raise ValueError(f"{name} 必须填写整数") from None


@lru_cache
def get_settings() -> Settings:
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
    return Settings(
        dashscope_api_key=os.getenv("DASHSCOPE_API_KEY"),
        qwen_model=os.getenv("QWEN_MODEL", "qwen-plus"),
        system_prompt=os.getenv(
            "SYSTEM_PROMPT",
            "你是一个友好、准确的中文 AI 助手。回答应清晰、简洁；不确定时应明确说明。",
        ),
        cors_origins=tuple(origin.strip() for origin in origins.split(",") if origin.strip()),
        auth_db_path=db_path,
        session_max_age=session_max_age,
        session_cookie_secure=os.getenv("SESSION_COOKIE_SECURE", "false").lower() == "true",
        smtp_host=os.getenv("SMTP_HOST", "").strip(),
        smtp_port=env_int("SMTP_PORT", 465),
        smtp_username=os.getenv("SMTP_USERNAME", "").strip(),
        smtp_password=os.getenv("SMTP_PASSWORD", ""),
        smtp_from=os.getenv("SMTP_FROM", "").strip(),
        smtp_security=os.getenv("SMTP_SECURITY", "ssl").lower(),
        smtp_timeout=env_int("SMTP_TIMEOUT", 10),
        email_code_secret=os.getenv("EMAIL_CODE_SECRET", ""),
        email_code_ttl=env_int("EMAIL_CODE_TTL", 300),
        email_code_cooldown=env_int("EMAIL_CODE_COOLDOWN", 60),
        email_code_max_attempts=env_int("EMAIL_CODE_MAX_ATTEMPTS", 5),
        chat_timeout=env_int("CHAT_TIMEOUT", 90),
        chat_context_chars=env_int("CHAT_CONTEXT_CHARS", 40_000),
        chat_max_output_tokens=env_int("CHAT_MAX_OUTPUT_TOKENS", 2048),
        chat_requests_per_minute=env_int("CHAT_REQUESTS_PER_MINUTE", 10),
        chat_requests_per_day=env_int("CHAT_REQUESTS_PER_DAY", 100),
        chat_global_requests_per_day=env_int("CHAT_GLOBAL_REQUESTS_PER_DAY", 1000),
        chat_user_concurrency=env_int("CHAT_USER_CONCURRENCY", 1),
        chat_global_concurrency=env_int("CHAT_GLOBAL_CONCURRENCY", 4),
        qwen_vision_model=os.getenv("QWEN_VISION_MODEL", "").strip(),
        attachment_max_bytes=env_int("ATTACHMENT_MAX_BYTES", 10 * 1024 * 1024),
        attachment_user_quota=env_int("ATTACHMENT_USER_QUOTA", 200 * 1024 * 1024),
        rag_enabled=os.getenv("RAG_ENABLED", "true").lower() == "true",
        qdrant_url=os.getenv("QDRANT_URL", "http://127.0.0.1:6333").rstrip("/"),
        qdrant_collection=os.getenv("QDRANT_COLLECTION", "zhiyan_documents_v1").strip(),
        embedding_model=os.getenv("EMBEDDING_MODEL", "text-embedding-v4").strip(),
        embedding_dimension=env_int("EMBEDDING_DIMENSION", 1024),
        qwen_coding_model=os.getenv("QWEN_CODING_MODEL", "").strip(),
        coding_max_steps=env_int("CODING_MAX_STEPS", 12),
        coding_task_timeout=env_int("CODING_TASK_TIMEOUT", 300),
        coding_tasks_per_day=env_int("CODING_TASKS_PER_DAY", 20),
        coding_global_concurrency=env_int("CODING_GLOBAL_CONCURRENCY", 4),
        coding_sandbox_enabled=os.getenv("CODING_SANDBOX_ENABLED", "false").lower() == "true",
        coding_sandbox_image=os.getenv("CODING_SANDBOX_IMAGE", "zhiyan-coding-python:1").strip(),
        coding_check_timeout=env_int("CODING_CHECK_TIMEOUT", 45),
    )
