from urllib.parse import urlsplit

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response
from fastapi.security import APIKeyCookie

from app.auth.schemas import AuthResponse, Credentials, EmailCodeResponse, MessageResponse, RegisterRequest, ResetPasswordRequest, SendEmailCodeRequest, UserResponse
from app.auth.security import DUMMY_PASSWORD_HASH, hash_password, verify_password
from app.auth.store import AuthStore, InvalidEmailCode, SessionChanged, TooManyAttempts, UsernameTaken
from app.auth.verification import EmailVerification
from app.config import Settings
from app.integrations.email import EmailUnavailable
from app.auth.account import AccountService, IncorrectPassword, SamePassword
from app.auth.schemas import RenameAccountRequest, ChangePasswordRequest


COOKIE_NAME = "zhiyan_session"
cookie_auth = APIKeyCookie(name=COOKIE_NAME, auto_error=False, description="先调用登录接口。浏览器通过 HttpOnly Cookie 自动携带凭证，不是手工填写的 Bearer Token。")


def get_auth_store(request: Request) -> AuthStore:
    return request.app.state.auth_store


def get_app_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_verification(request: Request) -> EmailVerification:
    return EmailVerification(request.app.state.auth_store, request.app.state.settings, request.app.state.mailer)


def require_trusted_origin(request: Request, settings: Settings = Depends(get_app_settings)):
    # 所有状态变更都检查 Origin/Referer；无浏览器头的 CLI 仍可使用。
    origin = request.headers.get("origin")
    if not origin and request.headers.get("referer"):
        parsed = urlsplit(request.headers["referer"])
        origin = f"{parsed.scheme}://{parsed.netloc}"
    allowed = {*settings.cors_origins, str(request.base_url).rstrip("/")}
    if origin and origin not in allowed:
        raise HTTPException(403, "请求来源不受信任，请从本站页面操作")
    if not origin and request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(403, "不允许跨站请求")


def require_user(token: str | None = Depends(cookie_auth), store: AuthStore = Depends(get_auth_store)) -> UserResponse:
    user = store.session_user(token)
    if user is None:
        raise HTTPException(401, "请先登录，或重新登录后继续")
    return user


def reserve_attempt(store: AuthStore, action: str, request: Request, username: str):
    try:
        store.reserve_auth_attempt(action, request.client.host if request.client else "unknown", username)
    except TooManyAttempts as exc:
        raise HTTPException(429, "尝试次数过多，请稍后再试", headers={"Retry-After": str(exc.retry_after)}) from exc


def set_session_cookie(response: Response, settings: Settings, token: str):
    response.set_cookie(
        COOKIE_NAME, token, max_age=settings.session_max_age, httponly=True,
        secure=settings.session_cookie_secure, samesite="lax", path="/api",
    )
    response.headers["Cache-Control"] = "no-store"


def sign_in(response: Response, request: Request, store: AuthStore, settings: Settings, user: UserResponse, version: int = 0):
    try:
        token = store.create_session(user.id, settings.session_max_age, request.cookies.get(COOKIE_NAME), expected_version=version)
    except SessionChanged:
        raise HTTPException(401, "账号密码已变更，请重新登录") from None
    set_session_cookie(response, settings, token)
    return AuthResponse(user=user)


router = APIRouter(prefix="/api/auth", tags=["账号"], dependencies=[Depends(require_trusted_origin)])


@router.post("/email-code", response_model=EmailCodeResponse, status_code=202, summary="申请注册或重置密码的邮箱验证码")
def send_email_code(data: SendEmailCodeRequest, request: Request, tasks: BackgroundTasks,
                    verification: EmailVerification = Depends(get_verification)):
    """202 仅表示已受理，投递在响应后进行；无论邮箱是否已注册，返回相同内容。"""
    try:
        record_id, code, deliver = verification.prepare(data.email, data.purpose, request.client.host if request.client else "unknown")
    except EmailUnavailable as exc:
        raise HTTPException(503, str(exc), headers={"X-Error-Code": exc.code}) from None
    except TooManyAttempts as exc:
        raise HTTPException(429, "验证码请求过于频繁，请稍后再试", headers={"Retry-After": str(exc.retry_after)}) from None
    if deliver:
        tasks.add_task(verification.deliver, record_id, code, data.email, data.purpose)
    return EmailCodeResponse(message="请求已受理。若该邮箱符合本次操作条件，你将收到验证码；请同时检查垃圾邮件。", retry_after=verification.settings.email_code_cooldown, expires_in=verification.settings.email_code_ttl)


def apply_verified_action(data, request, verification, purpose, password, username=None):
    try:
        verification.ensure_ready()
        verification.store.reserve_code_check(data.email, request.client.host if request.client else "unknown")
        return verification.store.apply_email_code(data.email, purpose, data.code.get_secret_value(), hash_password(password), verification.settings, username)
    except EmailUnavailable as exc:
        raise HTTPException(503, str(exc), headers={"X-Error-Code": exc.code}) from None
    except TooManyAttempts as exc:
        raise HTTPException(429, "核对次数过多，请稍后再试", headers={"Retry-After": str(exc.retry_after)}) from None
    except InvalidEmailCode:
        raise HTTPException(400, "验证码无效、已过期或不可用于本次操作，请检查邮箱和用途后重新申请") from None
    except UsernameTaken:
        raise HTTPException(409, "用户名已被使用或邮箱不可用于注册，请检查后重试") from None


@router.post("/register", response_model=AuthResponse, status_code=201, summary="通过邮箱验证码注册并登录")
def register(credentials: RegisterRequest, request: Request, response: Response,
             store: AuthStore = Depends(get_auth_store), settings: Settings = Depends(get_app_settings),
             verification: EmailVerification = Depends(get_verification)):
    reserve_attempt(store, "register", request, credentials.username)
    user = apply_verified_action(credentials, request, verification, "register", credentials.password.get_secret_value(), credentials.username)
    return sign_in(response, request, store, settings, user)


@router.post("/password/reset", response_model=MessageResponse, summary="通过邮箱验证码重置密码")
def reset_password(data: ResetPasswordRequest, request: Request, response: Response, tasks: BackgroundTasks,
                   verification: EmailVerification = Depends(get_verification)):
    """仅已验证邮箱可找回。成功后撤销该账号所有会话，不自动登录。"""
    user = apply_verified_action(data, request, verification, "reset_password", data.new_password.get_secret_value())
    tasks.add_task(verification.notify_password_changed, user.email)
    response.delete_cookie(COOKIE_NAME, path="/api", httponly=True, secure=verification.settings.session_cookie_secure, samesite="lax")
    return MessageResponse(message="密码已重置，所有设备的旧登录已失效。请使用新密码登录。")


@router.post("/login", response_model=AuthResponse)
def login(credentials: Credentials, request: Request, response: Response,
          store: AuthStore = Depends(get_auth_store), settings: Settings = Depends(get_app_settings)):
    reserve_attempt(store, "login", request, credentials.username)
    row = store.find_user(credentials.username)
    verified = verify_password(credentials.password.get_secret_value(), row["password_hash"] if row else DUMMY_PASSWORD_HASH)
    if not verified or row is None:
        raise HTTPException(401, "用户名或密码错误")
    return sign_in(response, request, store, settings, UserResponse(id=row["id"], username=row["username"], email=row["email"]), row["auth_version"])


@router.get("/me", response_model=AuthResponse)
def me(response: Response, user: UserResponse = Depends(require_user)):
    response.headers["Cache-Control"] = "no-store"
    return AuthResponse(user=user)


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response, store: AuthStore = Depends(get_auth_store),
           settings: Settings = Depends(get_app_settings)):
    store.revoke_session(request.cookies.get(COOKIE_NAME))
    response.delete_cookie(COOKIE_NAME, path="/api", httponly=True,
                           secure=settings.session_cookie_secure, samesite="lax")
    response.headers["Cache-Control"] = "no-store"


def perform_account_action(callback):
    try:
        return callback()
    except IncorrectPassword:
        raise HTTPException(400, "当前密码不正确，请重新输入", headers={"X-Error-Code": "CURRENT_PASSWORD_INVALID"}) from None
    except SamePassword:
        raise HTTPException(400, "新密码不能与当前密码相同") from None
    except UsernameTaken:
        raise HTTPException(409, "该用户名已被使用，请换一个用户名") from None
    except SessionChanged:
        raise HTTPException(401, "账号或登录状态已变化，请重新登录后操作") from None
    except TooManyAttempts as exc:
        raise HTTPException(429, "账号修改尝试过于频繁，请稍后再试", headers={"Retry-After": str(exc.retry_after)}) from None


@router.post("/username", response_model=AuthResponse, summary="已登录用户修改自己的登录用户名")
def rename_account(data: RenameAccountRequest, request: Request,
                   user: UserResponse = Depends(require_user), store: AuthStore = Depends(get_auth_store)):
    result = perform_account_action(lambda: AccountService(store).rename(
        user.id, request.cookies.get(COOKIE_NAME, ""), request.client.host if request.client else "unknown",
        data.username))
    return AuthResponse(user=result)


@router.post("/password/change", response_model=MessageResponse, summary="验证当前密码并改密，换发当前登录凭证、撤销其他登录")
def change_password(data: ChangePasswordRequest, request: Request, response: Response, tasks: BackgroundTasks,
                    user: UserResponse = Depends(require_user), store: AuthStore = Depends(get_auth_store),
                    settings: Settings = Depends(get_app_settings), verification: EmailVerification = Depends(get_verification)):
    result, token = perform_account_action(lambda: AccountService(store).change_password(
        user.id, request.cookies.get(COOKIE_NAME, ""), request.client.host if request.client else "unknown",
        data.current_password.get_secret_value(), data.new_password.get_secret_value(), settings.session_max_age))
    # 主动改密不依赖 SMTP 可用性；通知只作尽力投递，不影响事务成功。
    if result.email:
        tasks.add_task(verification.notify_password_changed, result.email)
    set_session_cookie(response, settings, token)
    return MessageResponse(message="密码已修改，当前浏览器保持登录，其他设备的旧登录已失效。")
