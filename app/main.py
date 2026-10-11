"""FastAPI application entry point."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from html import escape

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import HTTPException
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from .auth import require_app_password
from .config import (
    ALLOW_NO_AUTH,
    BACKGROUND_MAX_WORKERS,
    IMAP_FETCH_BATCH_SIZE,
    INITIAL_SYNC_LIMIT,
    SESSION_COOKIE_SECURE,
    SESSION_MAX_AGE_SEC,
    STATIC_DIR,
    SYNC_INTERVAL_MINUTES,
    SYNC_JUNK_ENABLED,
    SYNC_SENT_ENABLED,
    WEB_AUTH_ENABLED,
    load_session_secret,
    setup_logging,
)
from .csrf import verify_csrf
from .database import init_db
from .routes import auth as auth_routes
from .routes import accounts as accounts_routes
from .routes import attachments as attachments_routes
from .routes import events as events_routes
from .routes import messages as messages_routes
from .routes import senders as senders_routes
from .routes import send as send_routes
from .routes import sync as sync_routes
from .services.background import shutdown_executor
from .services.imap_backfill import shutdown_backfill, start_backfill
from .services.idle_service import shutdown_idle, start_idle
from .services.scheduler import shutdown_scheduler, start_scheduler
from .services.telegram_bot import shutdown_bot, start_bot
from .templating import templates

setup_logging()
logger = logging.getLogger(__name__)


def check_auth_configured(enabled: bool = WEB_AUTH_ENABLED, allow_no_auth: bool = ALLOW_NO_AUTH) -> None:
    """没有访问密码时拒绝启动，除非显式允许（fail-closed）。"""
    if not enabled and not allow_no_auth:
        raise RuntimeError(
            "WEB_AUTH_PASSWORD 未设置：拒绝启动，避免邮箱界面对外公开。"
            "请在 .env 中设置 WEB_AUTH_PASSWORD；确实不需要密码时设置 ALLOW_NO_AUTH=true。"
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    check_auth_configured()
    init_db()
    start_backfill()
    start_scheduler()
    start_idle()
    start_bot()
    logger.info(
        "Sync settings: sent_enabled=%s junk_enabled=%s initial_limit=%s fetch_batch=%s workers=%s interval=%s",
        SYNC_SENT_ENABLED,
        SYNC_JUNK_ENABLED,
        INITIAL_SYNC_LIMIT,
        IMAP_FETCH_BATCH_SIZE,
        BACKGROUND_MAX_WORKERS,
        SYNC_INTERVAL_MINUTES,
    )
    if WEB_AUTH_ENABLED:
        logger.info("Web password protection is enabled")
    else:
        logger.warning("WEB_AUTH_PASSWORD is not set and ALLOW_NO_AUTH=true; the web UI is publicly accessible")
    logger.info("Application startup complete")
    try:
        yield
    finally:
        shutdown_backfill()
        shutdown_bot()
        shutdown_idle()
        shutdown_scheduler()
        shutdown_executor()
        logger.info("Application shutdown complete")


app = FastAPI(
    title="Gmail Multi-Account Client",
    description="A simple multi-account Gmail client using IMAP/SMTP with app passwords.",
    version="0.1.0",
    lifespan=lifespan,
    dependencies=[Depends(verify_csrf)],
)

# 主页面的 CSP。邮件正文在 sandbox iframe 中渲染，有自己更严格的 CSP
# （见 services/mail_body.py），这里不会覆盖已存在的 CSP 头。
# 所有脚本、样式、字体都由本站提供（Bootstrap 在 static/vendor），不信任任何外部 CDN：
# 放行 CDN 域名等于允许执行该 CDN 上任何人发布的脚本。
_PAGE_CSP = "; ".join(
    [
        "default-src 'self'",
        # 不允许内联脚本：页面脚本都在 static/js，即使出现 XSS 注入的脚本也无法执行
        "script-src 'self'",
        "style-src 'self' 'unsafe-inline'",
        "font-src 'self' data:",
        "img-src 'self' data: blob: https:",
        "connect-src 'self'",
        "frame-src 'self'",
        "frame-ancestors 'self'",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
    ]
)


async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    headers = response.headers
    headers.setdefault("Content-Security-Policy", _PAGE_CSP)
    headers.setdefault("X-Content-Type-Options", "nosniff")
    headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    headers.setdefault("Referrer-Policy", "same-origin")
    return response


class ForwardedProtoMiddleware:
    """按 X-Forwarded-Proto 设置请求协议，与代理是否可信无关。

    nginx 终止 HTTPS 后用 http 转发过来，必须知道原始协议才能生成 https://
    的链接；否则 https 页面引用 http 资源会被浏览器拦截（样式丢失、表单无法提交）。
    伪造该头只会影响伪造者自己收到的页面，没有安全风险；
    客户端 IP 仍只采信可信代理（见 FORWARDED_ALLOW_IPS）。
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            for key, value in scope.get("headers", []):
                if key == b"x-forwarded-proto":
                    proto = value.decode("latin-1").split(",")[0].strip().lower()
                    if proto in ("http", "https"):
                        if scope["type"] == "websocket":
                            proto = "wss" if proto == "https" else "ws"
                        scope = dict(scope, scheme=proto)
                    break
        await self.app(scope, receive, send)


app.middleware("http")(require_app_password)
app.middleware("http")(add_security_headers)
app.add_middleware(
    SessionMiddleware,
    secret_key=load_session_secret(),
    session_cookie="gmail_client_session",
    max_age=SESSION_MAX_AGE_SEC,
    same_site="lax",
    https_only=SESSION_COOKIE_SECURE,
)

# 最外层：先确定协议，后面的中间件和路由生成的链接才是 https
app.add_middleware(ForwardedProtoMiddleware)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

app.include_router(auth_routes.router)
app.include_router(accounts_routes.router)
# senders 必须先于 messages：/messages/blocked-senders 不能被 /messages/{message_id} 抢先匹配
app.include_router(senders_routes.router)
app.include_router(messages_routes.router)
app.include_router(events_routes.router)
app.include_router(sync_routes.router)
app.include_router(send_routes.router)
app.include_router(attachments_routes.router)


@app.get("/", include_in_schema=False)
def index():
    return RedirectResponse(url="/messages/", status_code=302)


@app.get("/healthz", include_in_schema=False)
def healthz():
    return PlainTextResponse("ok")


# 错误页上显示的中文说明（接口内部仍使用英文 detail）
_ERROR_TEXT = {
    "Not Found": "找不到您要访问的页面。",
    "Message not found": "找不到这封邮件，它可能已被删除。",
    "Sent message not found": "找不到这条发件记录，它可能已被删除。",
    "Account not found": "找不到该账号。",
    "Account already exists": "该账号已经添加过了。",
    "App password is required": "请填写应用专用密码。",
    "App password is too short": "应用专用密码太短。",
    "At least one recipient is required": "请至少填写一个收件人。",
    "Attachment not found": "找不到该附件。",
    "Inline image not found": "找不到该图片。",
    "Blocked sender not found": "找不到该屏蔽记录。",
    "Trusted sender not found": "找不到该信任记录。",
    "Cannot parse sender email": "无法识别发件人地址。",
    "Invalid email address": "邮箱地址格式不正确。",
    "Selected account is not available": "所选账号不可用（可能已停用）。",
    "Invalid folder kind": "无效的文件夹。",
    "Invalid path": "无效的路径。",
    "Method Not Allowed": "不支持该操作。",
}


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    if 400 <= exc.status_code < 500:
        accept = request.headers.get("accept", "")
        if "text/html" in accept:
            detail = _ERROR_TEXT.get(str(exc.detail), exc.detail)
            return templates.TemplateResponse(
                request,
                "error.html",
                {"status_code": exc.status_code, "detail": detail},
                status_code=exc.status_code,
            )
    return HTMLResponse(
        content=f"<h1>{exc.status_code}</h1><p>{escape(str(exc.detail))}</p>",
        status_code=exc.status_code,
    )
