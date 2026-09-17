"""SMTP 只负责投递；验证码、账号及业务状态由认证模块管理。"""

from email.message import EmailMessage
import smtplib
import ssl
import socket
from typing import Protocol

from app.auth.schemas import normalize_email
from app.config import Settings
from app.observability import logger, request_id


class EmailUnavailable(Exception):
    def __init__(self, message: str, code="SMTP_UNAVAILABLE"):
        super().__init__(message)
        self.code = code


def smtp_error_code(error: Exception) -> str:
    if isinstance(error, smtplib.SMTPAuthenticationError):
        return "SMTP_AUTH_FAILED"
    if isinstance(error, smtplib.SMTPRecipientsRefused):
        return "SMTP_RECIPIENT_REJECTED"
    if isinstance(error, smtplib.SMTPSenderRefused):
        return "SMTP_SENDER_REJECTED"
    if isinstance(error, smtplib.SMTPDataError):
        return "SMTP_CONTENT_REJECTED"
    if isinstance(error, ssl.SSLError):
        return "SMTP_TLS_FAILED"
    if isinstance(error, socket.gaierror):
        return "SMTP_DNS_FAILED"
    if isinstance(error, TimeoutError):
        return "SMTP_TIMEOUT"
    return "SMTP_CONNECTION_FAILED"


class Mailer(Protocol):
    def ensure_ready(self) -> None: ...
    def send_code(self, email: str, code: str, purpose: str, ttl: int) -> None: ...
    def send_password_changed(self, email: str) -> None: ...


class SMTPMailer:
    def __init__(self, settings: Settings):
        self.settings = settings

    def ensure_ready(self):
        s = self.settings
        if not all((s.smtp_host, s.smtp_username, s.smtp_password, s.smtp_from)):
            raise EmailUnavailable("邮件服务尚未配置，请配置后端 SMTP_HOST、SMTP_USERNAME、SMTP_PASSWORD 和 SMTP_FROM", "SMTP_NOT_CONFIGURED")
        if s.smtp_host.lower() == "example.com" or s.smtp_host.lower().endswith(".example.com"):
            raise EmailUnavailable("SMTP_HOST 仍是示例地址，请填写邮箱服务商提供的真实 SMTP 地址", "SMTP_PLACEHOLDER_HOST")
        if s.smtp_security not in ("ssl", "starttls") or not 1 <= s.smtp_port <= 65535 or not 1 <= s.smtp_timeout <= 30:
            raise EmailUnavailable("邮件服务端口、加密方式或超时配置不正确")
        try:
            normalize_email(s.smtp_from)
        except ValueError:
            raise EmailUnavailable("邮件发件地址配置不正确") from None

    def _send(self, recipient: str, subject: str, content: str):
        self.ensure_ready()
        s = self.settings
        message = EmailMessage()
        message["From"] = s.smtp_from
        message["To"] = recipient
        message["Subject"] = subject
        message.set_content(content)
        context = ssl.create_default_context()
        accepted = False
        try:
            if s.smtp_security == "ssl":
                server = smtplib.SMTP_SSL(s.smtp_host, s.smtp_port, timeout=s.smtp_timeout, context=context)
            else:
                server = smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=s.smtp_timeout)
            with server:
                if s.smtp_security == "starttls":
                    server.ehlo()
                    server.starttls(context=context)
                    server.ehlo()
                server.login(s.smtp_username, s.smtp_password)
                if server.send_message(message):
                    raise EmailUnavailable("邮件投递未被服务器接受", "SMTP_RECIPIENT_REJECTED")
                accepted = True
        except (smtplib.SMTPException, OSError) as exc:
            if accepted:
                # SMTP 已接受 DATA 后，QUIT/关闭连接失败不能让已发送的验证码失效。
                logger.warning("smtp_close_failed request_id=%s category=%s accepted=true", request_id.get(), smtp_error_code(exc))
                return
            # 不透传 SMTP 返回值，避免泄露账号、邮箱和服务器详情。
            raise EmailUnavailable("邮件服务暂时无法投递，请稍后重试或检查发件配置", smtp_error_code(exc)) from None

    def send_code(self, email: str, code: str, purpose: str, ttl: int):
        label = "注册账号" if purpose == "register" else "重置密码"
        self._send(email, f"知言 AI · {label}验证码", (
            f"你正在知言 AI 中{label}。\n\n验证码：{code}\n有效期：{ttl} 秒。\n\n"
            "请仅使用最近一次申请的验证码，不要向他人提供验证码。\n"
            "验证码只能使用一次。如果不是你本人操作，请忽略此邮件。"
        ))

    def send_password_changed(self, email: str):
        self._send(email, "知言 AI · 密码已变更", "你的知言 AI 密码已变更，旧登录凭证已失效。\n若在账号设置中主动改密，操作所在浏览器会换发新凭证并保持登录；若通过邮箱找回密码，则需要重新登录。\n如果不是你本人操作，请立即通过可信入口重新设置密码并检查邮箱安全。")
