"""Login/logout routes for the app-wide password gate."""
from __future__ import annotations

import logging
import time
from urllib.parse import urlencode

from fastapi import APIRouter, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ..auth import (
    LoginThrottleStatus,
    clear_auth,
    clear_failed_logins,
    get_client_ip,
    get_login_throttle_status,
    global_failures_exceeded,
    mark_authenticated,
    password_matches,
    pop_auth_error,
    register_failed_login,
    safe_next_url,
    set_auth_error,
)
from ..config import (
    WEB_AUTH_BLOCKED_IPS,
    WEB_AUTH_ENABLED,
    WEB_AUTH_LOCKOUT_MINUTES,
    WEB_AUTH_MAX_FAILURES,
    WEB_AUTH_PASSWORD,
    WEB_AUTH_PERMANENT_BLOCK_AFTER_FAILURES,
)
from ..services import audit, two_factor
from ..templating import templates

logger = logging.getLogger(__name__)
router = APIRouter(tags=["auth"])


def _login_context(
    *,
    error: str | None,
    next_url: str,
    client_ip: str,
    throttle=None,
) -> dict:
    throttle = throttle or get_login_throttle_status("unknown")
    return {
        "error": error,
        "next_url": next_url,
        "client_ip": client_ip,
        "is_blocked": throttle.is_blocked,
        "is_permanent_blocked": throttle.is_permanent_blocked,
        "remaining_attempts": throttle.remaining_attempts,
        "remaining_lockout_minutes": throttle.remaining_lockout_minutes,
        "max_failures": WEB_AUTH_MAX_FAILURES,
        "lockout_minutes": WEB_AUTH_LOCKOUT_MINUTES,
        "permanent_block_after_failures": WEB_AUTH_PERMANENT_BLOCK_AFTER_FAILURES,
        "blocked_ips_count": len(WEB_AUTH_BLOCKED_IPS),
    }


def _redirect_to_login(
    request: Request,
    *,
    next_url: str,
    error: str | None = None,
) -> RedirectResponse:
    set_auth_error(request, error)
    login_url = str(request.url_for("auth.login_form"))
    return RedirectResponse(
        url=f"{login_url}?{urlencode({'next': next_url})}",
        status_code=303,
    )


@router.get("/login", response_class=HTMLResponse, name="auth.login_form")
def login_form(
    request: Request,
    next: str | None = Query(None),
    error: str | None = Query(None),
):
    if not WEB_AUTH_ENABLED:
        return RedirectResponse(url="/messages/", status_code=303)
    client_ip = get_client_ip(request)
    throttle = get_login_throttle_status(client_ip)
    session_error = pop_auth_error(request)
    error_message = error or session_error
    return templates.TemplateResponse(
        request,
        "login.html",
        _login_context(
            error=error_message,
            next_url=safe_next_url(next),
            client_ip=client_ip,
            throttle=throttle,
        ),
        status_code=429 if throttle.is_blocked else 200,
    )


@router.post("/login", name="auth.login_submit")
def login_submit(
    request: Request,
    password: str = Form(...),
    next: str | None = Form(None),
):
    if not WEB_AUTH_ENABLED:
        return RedirectResponse(url="/messages/", status_code=303)
    next_url = safe_next_url(next)
    ip_address = get_client_ip(request)
    throttle = get_login_throttle_status(ip_address)

    if throttle.is_blocked:
        error = "该 IP 已被永久封禁" if throttle.is_permanent_blocked else "该 IP 已被临时封禁"
        return _redirect_to_login(request, next_url=next_url, error=error)

    if global_failures_exceeded(ip_address):
        # 不检查密码，也不计入该 IP 的失败次数：攻击结束、时间窗口过去后自动恢复
        logger.warning("Login from unfamiliar IP %s rejected: global failure limit reached", ip_address)
        return _redirect_to_login(request, next_url=next_url, error="近期密码错误次数过多，暂时只允许以前登录过的网络登录")

    if not password_matches(password, WEB_AUTH_PASSWORD):
        throttle = register_failed_login(ip_address)
        audit.record("login_failed", f"第 {throttle.failed_attempts} 次", ip=ip_address)
        if throttle.is_blocked:
            audit.on_ip_blocked(ip_address, throttle.is_permanent_blocked)
            error = "该 IP 已被永久封禁" if throttle.is_permanent_blocked else "该 IP 已被临时封禁"
        else:
            error = "密码不正确"
        return _redirect_to_login(request, next_url=next_url, error=error)

    set_auth_error(request, None)
    if two_factor.is_enabled():
        # 密码正确，还要输入验证码。密码的失败计数等整个登录完成后才清零。
        # 验证码输错不封禁 IP，单次登录最多试 TWO_FACTOR_MAX_ATTEMPTS 次，之后需重新输入密码。
        request.session.clear()   # 防会话固定
        request.session[TWO_FACTOR_PENDING_KEY] = int(time.time())
        request.session[TWO_FACTOR_NEXT_KEY] = next_url
        return RedirectResponse(url=request.url_for("auth.login_2fa_form"), status_code=303)
    _complete_login(request, ip_address)
    return RedirectResponse(url=next_url, status_code=303)


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


# ---- 两步验证（第二步：输入认证器 App 中的 6 位验证码） ------------------------
TWO_FACTOR_PENDING_KEY = "2fa_pending_at"
TWO_FACTOR_NEXT_KEY = "2fa_next"
# 密码通过后必须在这段时间内输入验证码，否则重新输入密码
TWO_FACTOR_PENDING_SEC = 300


def _two_factor_pending(request: Request) -> bool:
    started = request.session.get(TWO_FACTOR_PENDING_KEY)
    return isinstance(started, int) and 0 <= time.time() - started <= TWO_FACTOR_PENDING_SEC


@router.get("/login/2fa", response_class=HTMLResponse, name="auth.login_2fa_form")
def login_2fa_form(request: Request):
    if not _two_factor_pending(request):
        return RedirectResponse(url=request.url_for("auth.login_form"), status_code=303)
    return templates.TemplateResponse(
        request, "login_2fa.html", {"error": pop_auth_error(request)}
    )


@router.post("/login/2fa", name="auth.login_2fa_submit")
def login_2fa_submit(request: Request, code: str = Form("")):
    next_url = safe_next_url(request.session.get(TWO_FACTOR_NEXT_KEY))
    if not _two_factor_pending(request):
        return _redirect_to_login(request, next_url=next_url, error="验证已超时")
    ip_address = get_client_ip(request)
    if get_login_throttle_status(ip_address).is_blocked:
        request.session.clear()
        return _redirect_to_login(request, next_url=next_url, error="该 IP 已被封禁")
    if not two_factor.verify_and_consume(code):
        # 验证码输错与密码输错一样计入该 IP（IPv6 按 /64）的失败次数，也计入全局限速。
        # 计数在服务端：重放旧 cookie、重新输入密码都不能清零，无法靠反复尝试猜出 6 位验证码。
        throttle = register_two_factor_failure(ip_address)
        if throttle.is_blocked:
            request.session.clear()
            error = "该 IP 已被永久封禁" if throttle.is_permanent_blocked else "验证码错误次数过多，该 IP 已被临时封禁"
            return _redirect_to_login(request, next_url=next_url, error=error)
        set_auth_error(request, f"验证码不正确，还可尝试 {throttle.remaining_attempts} 次")
        return RedirectResponse(url=request.url_for("auth.login_2fa_form"), status_code=303)
    _complete_login(request, ip_address)
    return RedirectResponse(url=next_url, status_code=303)


@router.post("/logout", name="auth.logout")
def logout(request: Request):
    if request.session.get("authenticated"):
        audit.record_request(request, "logout")
    clear_auth(request)
    return RedirectResponse(url="/login", status_code=303)
