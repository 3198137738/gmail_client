"""FastAPI application entry point."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from html import escape

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
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
    WEB_AUTH_PASSWORD,
    load_session_secret,
    setup_logging,
)
from .csrf import verify_csrf
from .database import init_db
from .routes import auth as auth_routes
from .routes import accounts as accounts_routes
from .routes import attachments as attachments_routes
from .routes import events as events_routes
from .routes import image_proxy as image_proxy_routes
from .routes import messages as messages_routes
from .routes import senders as senders_routes
from .routes import send as send_routes
from .routes import sync as sync_routes
from .routes import templates as templates_routes
from .services import image_proxy as image_proxy_service
from .services import login_sessions
from .services.background import shutdown_executor
from .services.imap_backfill import shutdown_backfill, start_backfill
from .services.idle_service import shutdown_idle, start_idle
from .services.scheduler import shutdown_scheduler, start_scheduler
from .services.telegram_bot import shutdown_bot, start_bot

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
    if WEB_AUTH_ENABLED:
        # 访问密码改过（.env 修改后重启）：撤销所有登录会话
        login_sessions.check_password_change(WEB_AUTH_PASSWORD)
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
# 所有脚本、样式、字体都由本站提供（前端构建产物在 static/dist），不信任任何外部 CDN：
# 放行 CDN 域名等于允许执行该 CDN 上任何人发布的脚本。
# 'unsafe-inline' 只用于样式：Naive UI 组件库运行时插入 <style> 标签。
_PAGE_CSP = "; ".join(
    [
        "default-src 'self'",
        # 不允许内联脚本：页面脚本都是 static/dist 中的文件，即使出现 XSS 注入的脚本也无法执行
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


# 浏览器在 Sec-Fetch-Dest 中标明请求用途。邮件正文里的 <img src="/messages/123">、
# CSS 背景图等会带着登录 cookie 请求本站页面（例如把邮件标为已读）；
# 这类「当作图片 / 样式加载」的请求只允许访问真正提供这些资源的路径。
_SUBRESOURCE_DESTS = {"image", "style", "script", "font", "audio", "video", "track", "object", "embed"}
_SUBRESOURCE_PATHS = ("/static/", "/attachments/", image_proxy_service.PROXY_PATH)


async def reject_misused_subresource(request: Request, call_next):
    dest = request.headers.get("sec-fetch-dest", "").lower()
    if dest in _SUBRESOURCE_DESTS and not request.url.path.startswith(_SUBRESOURCE_PATHS):
        return PlainTextResponse("Forbidden", status_code=403)
    return await call_next(request)


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
app.middleware("http")(reject_misused_subresource)
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

IMMUTABLE_DIRS = ("dist/assets/",)


class CachedStaticFiles(StaticFiles):
    """静态资源的缓存策略。

    - 前端构建产物 ``dist/assets/`` 的文件名带内容哈希（Vite 生成），内容永不改变，
      可以长期缓存：一年 + immutable，浏览器连协商请求都不发。带 ``?v=`` 的地址同理。
    - 其余（logo、favicon 等不带版本的地址）缓存 1 小时。
    """

    _IMMUTABLE = "public, max-age=31536000, immutable"
    _SHORT = "public, max-age=3600"

    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        if response.status_code in (200, 304):
            rel = path.replace("\\", "/")
            versioned = b"v=" in (scope.get("query_string") or b"")
            if versioned or rel.startswith(IMMUTABLE_DIRS):
                response.headers["Cache-Control"] = self._IMMUTABLE
            else:
                response.headers["Cache-Control"] = self._SHORT
        return response


# check_dir=False：前端未构建时（新克隆的仓库）static/dist 不存在，应用仍能启动
app.mount("/static", CachedStaticFiles(directory=str(STATIC_DIR), check_dir=False), name="static")

app.include_router(auth_routes.router)
app.include_router(accounts_routes.router)
app.include_router(accounts_routes.oauth_router)
app.include_router(senders_routes.router)
app.include_router(messages_routes.router)
app.include_router(messages_routes.body_router)
app.include_router(events_routes.router)
app.include_router(sync_routes.router)
app.include_router(templates_routes.router)
app.include_router(send_routes.router)
app.include_router(send_routes.body_router)
app.include_router(attachments_routes.router)
app.include_router(image_proxy_routes.router)


@app.get("/", include_in_schema=False)
def index():
    return RedirectResponse(url="/messages/", status_code=302)


@app.get("/healthz", include_in_schema=False)
def healthz():
    return PlainTextResponse("ok")


# 接口错误的中文说明（代码里的 detail 仍使用英文）
_ERROR_TEXT = {
    "Not Found": "找不到您要访问的内容。",
    "Message not found": "找不到这封邮件，它可能已被删除。",
    "Sent message not found": "找不到这条发件记录，它可能已被删除。",
    "Draft not found": "找不到这封草稿，它可能已被发送或删除。",
    "Account not found": "找不到该账号。",
    "Account already exists": "该账号已经添加过了。",
    "App password is required": "请填写应用专用密码（至少 8 位）。",
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
    "Unknown action": "不支持该操作。",
    "Method Not Allowed": "不支持该操作。",
}


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    detail = _ERROR_TEXT.get(str(exc.detail), str(exc.detail))
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": detail}, status_code=exc.status_code, headers=exc.headers)
    return HTMLResponse(
        content=f"<h1>{exc.status_code}</h1><p>{escape(detail)}</p>",
        status_code=exc.status_code,
    )


# ---- 前端（Vue 单页应用） ------------------------------------------------------
# 构建产物（frontend/ 中 npm run build）在 static/dist；除接口和上面的路由外，
# 其余地址（/messages/、/accounts/ 等）都返回同一个 index.html，由前端路由决定显示哪个页面。
SPA_INDEX = STATIC_DIR / "dist" / "index.html"
# 这些前缀下是接口和文件，不是页面：匹配不到时返回 404，而不是前端页面
_NOT_PAGE_PREFIXES = ("api/", "static/", "attachments/", "events/", image_proxy_service.PROXY_PATH.lstrip("/"))


@app.get("/{full_path:path}", include_in_schema=False)
def spa_index(full_path: str):
    if full_path.startswith("api/"):
        return JSONResponse({"detail": _ERROR_TEXT["Not Found"]}, status_code=404)
    if full_path.startswith(_NOT_PAGE_PREFIXES):
        return PlainTextResponse("Not Found", status_code=404)
    if not SPA_INDEX.is_file():
        return PlainTextResponse(
            "前端尚未构建：请在 frontend 目录执行 npm ci && npm run build", status_code=503
        )
    # index.html 引用的脚本文件名带内容哈希，index.html 本身不能缓存，否则更新后拿不到新版本
    return FileResponse(SPA_INDEX, headers={"Cache-Control": "no-cache"})
