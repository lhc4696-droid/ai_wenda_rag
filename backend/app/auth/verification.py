"""验证码投递在响应后执行，不通过响应差异暴露账号是否存在。"""

from app.auth.store import AuthStore
from app.config import Settings
from app.integrations.email import EmailUnavailable, Mailer
from app.observability import logger, request_id


class EmailVerification:
    def __init__(self, store: AuthStore, settings: Settings, mailer: Mailer):
        self.store, self.settings, self.mailer = store, settings, mailer

    def ensure_ready(self):
        s = self.settings
        if len(s.email_code_secret) < 32:
            raise EmailUnavailable("邮件验证码尚未配置，请设置至少 32 个字符的随机 EMAIL_CODE_SECRET", "EMAIL_SECRET_INVALID")
        if not (60 <= s.email_code_ttl <= 1800 and 30 <= s.email_code_cooldown <= s.email_code_ttl and 1 <= s.email_code_max_attempts <= 10):
            raise EmailUnavailable("邮箱验证码有效期、发送间隔或核对次数配置不正确")
        self.mailer.ensure_ready()

    def prepare(self, email: str, purpose: str, ip: str):
        self.ensure_ready()
        return self.store.prepare_email_code(email, purpose, ip, self.settings)

    def deliver(self, record_id: str, code: str, email: str, purpose: str):
        if not self.store.delivery_is_pending(record_id):
            return
        try:
            self.mailer.send_code(email, code, purpose, self.settings.email_code_ttl)
        except Exception as exc:
            self.store.finish_delivery(record_id, False)
            category = exc.code if isinstance(exc, EmailUnavailable) else "SMTP_UNEXPECTED_ERROR"
            logger.warning("email_delivery_failed request_id=%s record=%s code=%s", request_id.get(), record_id, category)
        else:
            self.store.finish_delivery(record_id, True)

    def notify_password_changed(self, email: str):
        try:
            self.mailer.send_password_changed(email)
        except Exception as exc:
            category = exc.code if isinstance(exc, EmailUnavailable) else "SMTP_UNEXPECTED_ERROR"
            logger.warning("password_notification_failed request_id=%s code=%s", request_id.get(), category)
