"""Login/logout routes for the app-wide password gate."""
from __future__ import annotations

import time
from urllib.parse import urlencode

from fastapi import APIRouter, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ..auth import (
    clear_auth,
    clear_failed_logins,
    get_client_ip,
    get_login_throttle_status,
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
from ..services import two_factor
from ..templating import templates

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
        if throttle.is_permanent_blocked:
            error = (
                "\u8be5 IP \u5df2\u88ab\u6c38\u4e45\u5c01\u7981\uff0c"
                "\u65e0\u6cd5\u7ee7\u7eed\u767b\u5f55"
            )
        else:
            error = (
                f"\u8be5 IP \u5df2\u88ab\u4e34\u65f6\u5c01\u7981\uff0c"
                f"\u8bf7\u5728\u7ea6 {throttle.remaining_lockout_minutes} "
                "\u5206\u949f\u540e\u518d\u8bd5"
            )
        return _redirect_to_login(request, next_url=next_url, error=error)

    if not password_matches(password, WEB_AUTH_PASSWORD):
        throttle = register_failed_login(ip_address)
        if throttle.is_blocked:
            if throttle.is_permanent_blocked:
                error = (
                    f"\u8fde\u7eed\u8f93\u9519\u8fbe\u5230 {WEB_AUTH_MAX_FAILURES} "
                    "\u6b21\uff0c\u8be5 IP \u5df2\u88ab\u6c38\u4e45\u5c01\u7981"
                )
            else:
                error = (
                    f"\u8fde\u7eed\u8f93\u9519\u8fbe\u5230 {WEB_AUTH_MAX_FAILURES} "
                    f"\u6b21\uff0c\u8be5 IP \u5df2\u88ab\u5c01\u7981 "
                    f"{WEB_AUTH_LOCKOUT_MINUTES} \u5206\u949f"
                )
        else:
            next_block_text = (
                "\u6c38\u4e45\u5c01\u7981\u8be5 IP"
                if WEB_AUTH_PERMANENT_BLOCK_AFTER_FAILURES
                else f"\u5c01\u7981 IP {WEB_AUTH_LOCKOUT_MINUTES} \u5206\u949f"
            )
            error = (
                f"\u5bc6\u7801\u4e0d\u6b63\u786e\uff0c\u8fd8\u53ef\u518d\u5c1d\u8bd5 "
                f"{throttle.remaining_attempts} \u6b21\uff1b"
                f"\u8fbe\u5230 {WEB_AUTH_MAX_FAILURES} \u6b21\u5c06{next_block_text}"
            )
        return _redirect_to_login(request, next_url=next_url, error=error)

    set_auth_error(request, None)
    if two_factor.is_enabled():
        # 密码正确，还要输入验证码。密码的失败计数等整个登录完成后才清零。
        # 验证码输错不封禁 IP，单次登录最多试 TWO_FACTOR_MAX_ATTEMPTS 次，之后需重新输入密码。
        request.session.clear()   # 防会话固定
        request.session[TWO_FACTOR_PENDING_KEY] = int(time.time())
        request.session[TWO_FACTOR_NEXT_KEY] = next_url
        return RedirectResponse(url=request.url_for("auth.login_2fa_form"), status_code=303)
    clear_failed_logins(ip_address)
    mark_authenticated(request)
    return RedirectResponse(url=next_url, status_code=303)


# ---- 两步验证（第二步：输入认证器 App 中的 6 位验证码） ------------------------
TWO_FACTOR_PENDING_KEY = "2fa_pending_at"
TWO_FACTOR_NEXT_KEY = "2fa_next"
# 密码通过后必须在这段时间内输入验证码，否则重新输入密码
TWO_FACTOR_PENDING_SEC = 300
TWO_FACTOR_ATTEMPTS_KEY = "2fa_attempts"
# 验证码输错不封禁 IP；同一次登录连续输错这么多次后退回密码步骤（不是封禁，重新输密码即可），
# 防止已知密码的人在一次登录中无限猜测 6 位验证码
TWO_FACTOR_MAX_ATTEMPTS = 10


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
        return _redirect_to_login(request, next_url=next_url, error="验证超时，请重新输入密码")
    ip_address = get_client_ip(request)
    if get_login_throttle_status(ip_address).is_blocked:
        request.session.clear()
        return _redirect_to_login(request, next_url=next_url, error="该 IP 已被封禁，请稍后再试")
    if not two_factor.verify_and_consume(code):
        # 验证码输错不计入失败次数、不封禁 IP
        attempts = int(request.session.get(TWO_FACTOR_ATTEMPTS_KEY) or 0) + 1
        if attempts >= TWO_FACTOR_MAX_ATTEMPTS:
            request.session.clear()
            return _redirect_to_login(
                request, next_url=next_url, error="验证码错误次数过多，请重新输入密码后再试"
            )
        request.session[TWO_FACTOR_ATTEMPTS_KEY] = attempts
        set_auth_error(
            request,
            "验证码不正确。同一个验证码只能用一次，请等 App 中出现新的验证码；"
            "仍不对时检查手机时间是否准确",
        )
        return RedirectResponse(url=request.url_for("auth.login_2fa_form"), status_code=303)
    clear_failed_logins(ip_address)
    mark_authenticated(request)   # 会清空会话（含待验证状态）
    return RedirectResponse(url=next_url, status_code=303)


@router.post("/logout", name="auth.logout")
def logout(request: Request):
    clear_auth(request)
    return RedirectResponse(url="/login", status_code=303)
