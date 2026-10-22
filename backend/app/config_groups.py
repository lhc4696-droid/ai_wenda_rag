"""配置按功能分组，便于维护和理解"""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class SMTPConfig:
    """邮件服务配置"""
    host: str = ""
    port: int = 465
    username: str = ""
    password: str = field(default="", repr=False)
    from_address: str = ""
    security: str = "ssl"
    timeout: int = 10


@dataclass(frozen=True)
class EmailCodeConfig:
    """邮箱验证码配置"""
    secret: str = field(default="", repr=False)
    ttl: int = 300  # 验证码有效期（秒）
    cooldown: int = 60  # 发送冷却时间（秒）
    max_attempts: int = 5  # 最大验证尝试次数


@dataclass(frozen=True)
class ChatConfig:
    """聊天服务配置"""
    timeout: int = 90  # 单次请求超时（秒）
    context_chars: int = 40_000  # 上下文字符预算
    max_output_tokens: int = 2048  # 最大输出token数
    requests_per_minute: int = 10  # 每用户每分钟请求限制
    requests_per_day: int = 100  # 每用户每日请求限制
    global_requests_per_day: int = 1000  # 全局每日请求限制
    user_concurrency: int = 1  # 每用户并发限制
    global_concurrency: int = 4  # 全局并发限制

    def validate(self, system_prompt_length: int):
        """验证配置合理性"""
        if not 5 <= self.timeout <= 100:
            raise ValueError("CHAT_TIMEOUT 必须为 5–100 秒，低于前端 120 秒等待上限")
        if not 20_000 <= self.context_chars <= 200_000:
            raise ValueError("CHAT_CONTEXT_CHARS 必须为 20000–200000")
        if system_prompt_length >= self.context_chars:
            raise ValueError("SYSTEM_PROMPT 超出 CHAT_CONTEXT_CHARS 预算")
        if not 1 <= self.max_output_tokens <= 8192:
            raise ValueError("CHAT_MAX_OUTPUT_TOKENS 必须为 1–8192")
        for name, value in [
            ("requests_per_minute", self.requests_per_minute),
            ("requests_per_day", self.requests_per_day),
            ("global_requests_per_day", self.global_requests_per_day),
            ("user_concurrency", self.user_concurrency),
            ("global_concurrency", self.global_concurrency),
        ]:
            if value < 1:
                raise ValueError(f"CHAT_{name.upper()} 必须大于 0")


@dataclass(frozen=True)
class AttachmentConfig:
    """附件配置"""
    max_bytes: int = 10 * 1024 * 1024  # 单个附件大小限制
    user_quota: int = 200 * 1024 * 1024  # 用户总附件配额

    def validate(self):
        """验证配置合理性"""
        if not 1 <= self.max_bytes <= 10 * 1024 * 1024:
            raise ValueError("ATTACHMENT_MAX_BYTES 必须在 1–10485760 之间")
        if self.user_quota < self.max_bytes + 3 * 1024 * 1024:
            raise ValueError("ATTACHMENT_USER_QUOTA 至少为单个附件上限加 3145728 字节预览预留空间")


@dataclass(frozen=True)
class RagConfig:
    """RAG检索配置"""
    enabled: bool = False
    qdrant_url: str = "http://127.0.0.1:6333"
    qdrant_collection: str = "zhiyan_documents_v1"
    embedding_model: str = "text-embedding-v4"
    embedding_dimension: int = 1024
    requests_per_day: int = 100
    global_requests_per_day: int = 1000

    def validate(self):
        """验证配置合理性"""
        if not self.embedding_model:
            raise ValueError("EMBEDDING_MODEL 不能为空")
        if not 64 <= self.embedding_dimension <= 4096:
            raise ValueError("EMBEDDING_DIMENSION 必须在 64–4096 之间，并与模型实际输出一致")
        if self.requests_per_day < 1 or self.global_requests_per_day < 1:
            raise ValueError("RAG_EMBEDDING_REQUESTS_PER_DAY 和 RAG_EMBEDDING_GLOBAL_REQUESTS_PER_DAY 必须大于 0")

        from urllib.parse import urlsplit
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


@dataclass(frozen=True)
class CodingConfig:
    """Coding Agent配置"""
    max_steps: int = 12  # 单次任务最大步骤数
    task_timeout: int = 300  # 任务超时时间（秒）
    tasks_per_day: int = 20  # 每用户每日任务限制
    global_concurrency: int = 4  # 全局并发限制
    sandbox_enabled: bool = False  # 是否启用沙箱
    sandbox_image: str = "zhiyan-coding-python:1"  # 沙箱镜像名
    check_timeout: int = 45  # 检查超时时间（秒）

    def validate(self):
        """验证配置合理性"""
        if not 1 <= self.max_steps <= 30:
            raise ValueError("CODING_MAX_STEPS 必须在 1–30 之间")
        if not 60 <= self.task_timeout <= 900:
            raise ValueError("CODING_TASK_TIMEOUT 必须在 60–900 秒之间")
        if not 5 <= self.check_timeout <= 120:
            raise ValueError("CODING_CHECK_TIMEOUT 必须在 5–120 秒之间")
        if not 1 <= self.tasks_per_day <= 100 or not 1 <= self.global_concurrency <= 16:
            raise ValueError("Coding Agent 每日任务上限为 1–100，并发上限为 1–16")
        if not self.sandbox_image or self.sandbox_image.startswith("-") or any(c.isspace() for c in self.sandbox_image):
            raise ValueError("CODING_SANDBOX_IMAGE 必须为有效的本地镜像名称")


@dataclass(frozen=True)
class CustomModelConfig:
    """自定义模型代理配置"""
    proxy_url: str = field(default="", repr=False)
    proxy_base_urls: tuple[str, ...] = field(default=(), repr=False)
