"""FastAPI application entry point."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import HTTPException
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from .auth import require_app_password
from .config import (
    BACKGROUND_MAX_WORKERS,
    IMAP_FETCH_BATCH_SIZE,
    INITIAL_SYNC_LIMIT,
    STATIC_DIR,
    SYNC_INTERVAL_MINUTES,
    SYNC_SENT_ENABLED,
    WEB_AUTH_ENABLED,
    load_session_secret,
    setup_logging,
)
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
        "Sync settings: sent_enabled=%s initial_limit=%s fetch_batch=%s workers=%s interval=%s",
        SYNC_SENT_ENABLED,
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
)

app.middleware("http")(require_app_password)
app.add_middleware(
    SessionMiddleware,
    secret_key=load_session_secret(),
    session_cookie="gmail_client_session",
    same_site="lax",
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
        content=f"<h1>{exc.status_code}</h1><p>{exc.detail}</p>",
        status_code=exc.status_code,
    )
