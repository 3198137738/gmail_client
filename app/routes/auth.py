"""登录 / 两步验证 / 登出接口（访问密码保护）。

前端打开时先请求 ``GET /api/session``：拿到 CSRF token、是否已登录、本 IP 的封禁状态等。
登录成功后会话被清空重建（防会话固定），响应中带回新的 CSRF token。
"""
from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ..auth import (
    LoginThrottleStatus,
    clear_auth,
    clear_failed_logins,
    get_client_ip,
    get_login_throttle_status,
    global_failures_exceeded,
    is_authenticated,
    mark_authenticated,
    password_matches,
    register_failed_login,
)
from ..config import (
    REALTIME_ENABLED,
    SYNC_JUNK_ENABLED,
    SYNC_TRASH_ENABLED,
    UNDO_SEND_SECONDS,
    WEB_AUTH_ENABLED,
    WEB_AUTH_PASSWORD,
)
from ..csrf import get_csrf_token
from ..services import audit, two_factor

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["auth"])

# ---- 两步验证（第二步：输入认证器 App 中的 6 位验证码） ------------------------
TWO_FACTOR_PENDING_KEY = "2fa_pending_at"
# 密码通过后必须在这段时间内输入验证码，否则重新输入密码
TWO_FACTOR_PENDING_SEC = 300


def _two_factor_pending(request: Request) -> bool:
    started = request.session.get(TWO_FACTOR_PENDING_KEY)
    return isinstance(started, int) and 0 <= time.time() - started <= TWO_FACTOR_PENDING_SEC


def _fail(message: str, status_code: int = 400, **extra) -> JSONResponse:
    return JSONResponse({"detail": message, **extra}, status_code=status_code)


def _blocked_message(throttle: LoginThrottleStatus) -> str:
    return "该 IP 已被永久封禁" if throttle.is_permanent_blocked else "该 IP 已被临时封禁"


@router.get("/session", name="auth.session")
def session_info(request: Request):
    """前端启动时调用：登录状态、CSRF token 和界面配置。"""
    client_ip = get_client_ip(request)
    throttle = get_login_throttle_status(client_ip)
    return {
        "authenticated": (not WEB_AUTH_ENABLED) or is_authenticated(request),
        "auth_enabled": WEB_AUTH_ENABLED,
        "two_factor_pending": _two_factor_pending(request),
        "csrf_token": get_csrf_token(request),
        "client_ip": client_ip,
        "blocked": throttle.is_blocked,
        "permanent_blocked": throttle.is_permanent_blocked,
        "config": {
            "realtime_enabled": REALTIME_ENABLED,
            "sync_junk_enabled": SYNC_JUNK_ENABLED,
            "sync_trash_enabled": SYNC_TRASH_ENABLED,
            "undo_send_seconds": UNDO_SEND_SECONDS,
        },
    }


class LoginBody(BaseModel):
    password: str = ""


@router.post("/login", name="auth.login")
def login(request: Request, body: LoginBody):
    if not WEB_AUTH_ENABLED:
        return {"ok": True, "two_factor": False, "csrf_token": get_csrf_token(request)}
    ip_address = get_client_ip(request)
    throttle = get_login_throttle_status(ip_address)

    if throttle.is_blocked:
        return _fail(_blocked_message(throttle), 429, blocked=True)

    if global_failures_exceeded(ip_address):
        # 不检查密码，也不计入该 IP 的失败次数：攻击结束、时间窗口过去后自动恢复
        logger.warning("Login from unfamiliar IP %s rejected: global failure limit reached", ip_address)
        return _fail("近期密码错误次数过多，暂时只允许以前登录过的网络登录", 429)

    if not password_matches(body.password, WEB_AUTH_PASSWORD):
        throttle = register_failed_login(ip_address)
        audit.record("login_failed", f"第 {throttle.failed_attempts} 次", ip=ip_address)
        if throttle.is_blocked:
            audit.on_ip_blocked(ip_address, throttle.is_permanent_blocked)
            return _fail(_blocked_message(throttle), 429, blocked=True)
        return _fail("密码不正确", 400)

    if two_factor.is_enabled():
        # 密码正确，还要输入验证码。密码的失败计数等整个登录完成后才清零。
        request.session.clear()   # 防会话固定
        request.session[TWO_FACTOR_PENDING_KEY] = int(time.time())
        return {"ok": True, "two_factor": True, "csrf_token": get_csrf_token(request)}
    _complete_login(request, ip_address)
    return {"ok": True, "two_factor": False, "csrf_token": get_csrf_token(request)}


def register_two_factor_failure(ip_address: str, detail: str = "登录") -> LoginThrottleStatus:
    """验证码输错：计入该 IP 的失败次数（达到上限即封禁）并写审计日志（计入全局限速）。"""
    throttle = register_failed_login(ip_address)
    audit.record("login_2fa_failed", f"{detail}，第 {throttle.failed_attempts} 次", ip=ip_address)
    if throttle.is_blocked:
        audit.on_ip_blocked(ip_address, throttle.is_permanent_blocked)
    return throttle


def _complete_login(request: Request, ip_address: str) -> None:
    clear_failed_logins(ip_address)
    mark_authenticated(request)   # 会清空会话（含两步验证的待验证状态）
    audit.on_login_success(ip_address, request.headers.get("user-agent"))


class CodeBody(BaseModel):
    code: str = ""


@router.post("/login/2fa", name="auth.login_2fa")
def login_2fa(request: Request, body: CodeBody):
    if not _two_factor_pending(request):
        return _fail("验证已超时，请重新输入密码", 400, expired=True)
    ip_address = get_client_ip(request)
    if get_login_throttle_status(ip_address).is_blocked:
        request.session.clear()
        return _fail("该 IP 已被封禁", 429, expired=True)
    if not two_factor.verify_and_consume(body.code):
        # 验证码输错与密码输错一样计入该 IP（IPv6 按 /64）的失败次数，也计入全局限速。
        # 计数在服务端：重放旧 cookie、重新输入密码都不能清零，无法靠反复尝试猜出 6 位验证码。
        throttle = register_two_factor_failure(ip_address)
        if throttle.is_blocked:
            request.session.clear()
            message = "该 IP 已被永久封禁" if throttle.is_permanent_blocked else "验证码错误次数过多，该 IP 已被临时封禁"
            return _fail(message, 429, expired=True)
        return _fail(f"验证码不正确，还可尝试 {throttle.remaining_attempts} 次", 400)
    _complete_login(request, ip_address)
    return {"ok": True, "csrf_token": get_csrf_token(request)}


@router.post("/logout", name="auth.logout")
def logout(request: Request):
    if request.session.get("authenticated"):
        audit.record_request(request, "logout")
    clear_auth(request)
    return {"ok": True}
