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
from .routes import send as send_routes
from .routes import sync as sync_routes
from .services.background import shutdown_executor
from .services.idle_service import shutdown_idle, start_idle
from .services.scheduler import shutdown_scheduler, start_scheduler
from .services.telegram_bot import shutdown_bot, start_bot
from .templating import templates

setup_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
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
        logger.warning("WEB_AUTH_PASSWORD is not set; the web UI is publicly accessible")
    logger.info("Application startup complete")
    try:
        yield
    finally:
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
_CDN = "https://cdn.jsdelivr.net"
_PAGE_CSP = "; ".join(
    [
        "default-src 'self'",
        # 不允许内联脚本：页面脚本都在 static/js，即使出现 XSS 注入的脚本也无法执行
        f"script-src 'self' {_CDN}",
        f"style-src 'self' 'unsafe-inline' {_CDN}",
        f"font-src 'self' data: {_CDN}",
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

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

app.include_router(auth_routes.router)
app.include_router(accounts_routes.router)
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


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    if 400 <= exc.status_code < 500:
        accept = request.headers.get("accept", "")
        if "text/html" in accept:
            return templates.TemplateResponse(
                request,
                "error.html",
                {"status_code": exc.status_code, "detail": exc.detail},
                status_code=exc.status_code,
            )
    return HTMLResponse(
        content=f"<h1>{exc.status_code}</h1><p>{escape(str(exc.detail))}</p>",
        status_code=exc.status_code,
    )
