"""App-wide password gate backed by signed cookie sessions."""
from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from ipaddress import ip_address, ip_network
from urllib.parse import urlencode, urlparse

from fastapi import Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from .config import (
    WEB_AUTH_BLOCKED_IPS,
    WEB_AUTH_ENABLED,
    WEB_AUTH_LOCKOUT_MINUTES,
    WEB_AUTH_MAX_FAILURES,
    WEB_AUTH_PERMANENT_BLOCK_AFTER_FAILURES,
    TRUST_PROXY_HEADERS,
    TRUSTED_PROXY_NETWORKS,
)
from .database import session_scope
from .models import LoginThrottle

logger = logging.getLogger(__name__)

AUTH_SESSION_KEY = "authenticated"
AUTH_ERROR_SESSION_KEY = "auth_error"
LOGIN_PATH = "/login"
LOGOUT_PATH = "/logout"
PUBLIC_PATH_PREFIXES = ("/static/", "/healthz")
PUBLIC_PATHS = {LOGIN_PATH, LOGOUT_PATH}


@dataclass
class LoginThrottleStatus:
    ip_address: str
    failed_attempts: int = 0
    is_permanent_blocked: bool = False
    blocked_until: datetime | None = None

    @property
    def is_blocked(self) -> bool:
        return self.is_permanent_blocked or (
            self.blocked_until is not None and self.blocked_until > datetime.utcnow()
        )

    @property
    def remaining_attempts(self) -> int:
        return max(WEB_AUTH_MAX_FAILURES - self.failed_attempts, 0)

    @property
    def remaining_lockout_minutes(self) -> int:
        if self.is_permanent_blocked or not self.is_blocked or self.blocked_until is None:
            return 0
        seconds = max((self.blocked_until - datetime.utcnow()).total_seconds(), 0)
        return max(1, int((seconds + 59) // 60))


def is_authenticated(request: Request) -> bool:
    return bool(request.session.get(AUTH_SESSION_KEY))


def mark_authenticated(request: Request) -> None:
    request.session[AUTH_SESSION_KEY] = True


def clear_auth(request: Request) -> None:
    request.session.pop(AUTH_SESSION_KEY, None)


def set_auth_error(request: Request, message: str | None) -> None:
    if message:
        request.session[AUTH_ERROR_SESSION_KEY] = message
        return
    request.session.pop(AUTH_ERROR_SESSION_KEY, None)


def pop_auth_error(request: Request) -> str | None:
    value = request.session.pop(AUTH_ERROR_SESSION_KEY, None)
    return value if isinstance(value, str) else None


def is_public_path(path: str) -> bool:
    if path in PUBLIC_PATHS:
        return True
    return any(path.startswith(prefix) for prefix in PUBLIC_PATH_PREFIXES)


def _normalize_ip(value: str | None) -> str | None:
    if not value:
        return None
    candidate = value.strip().strip('"').strip("'")
    if not candidate or candidate.lower() == "unknown":
        return None

    try:
        return str(ip_address(candidate))
    except ValueError:
        pass

    if candidate.startswith("[") and "]" in candidate:
        inner = candidate[1:candidate.index("]")]
        try:
            return str(ip_address(inner))
        except ValueError:
            return None

    if candidate.count(":") == 1 and "." in candidate:
        host, _sep, port = candidate.rpartition(":")
        if port.isdigit():
            try:
                return str(ip_address(host))
            except ValueError:
                return None

    return None


def _is_trusted_proxy(ip_value: str | None) -> bool:
    if not TRUST_PROXY_HEADERS:
        return False
    normalized = _normalize_ip(ip_value)
    if not normalized:
        return False
    try:
        addr = ip_address(normalized)
    except ValueError:
        return False

    for network_text in TRUSTED_PROXY_NETWORKS:
        try:
            if addr in ip_network(network_text, strict=False):
                return True
        except ValueError:
            continue
    return False


def get_client_ip(request: Request) -> str:
    direct_ip = _normalize_ip(request.client.host if request.client else None)

    if direct_ip and _is_trusted_proxy(direct_ip):
        forwarded_for = request.headers.get("x-forwarded-for", "")
        if forwarded_for:
            for part in forwarded_for.split(","):
                forwarded_ip = _normalize_ip(part)
                if forwarded_ip:
                    return forwarded_ip[:255]

        real_ip = _normalize_ip(request.headers.get("x-real-ip"))
        if real_ip:
            return real_ip[:255]

    if direct_ip:
        return direct_ip[:255]
    return "unknown"


def is_explicitly_blocked_ip(ip_address: str) -> bool:
    return ip_address in WEB_AUTH_BLOCKED_IPS


def _get_or_create_throttle(db, ip_address: str) -> LoginThrottle:
    state = db.execute(
        select(LoginThrottle).where(LoginThrottle.ip_address == ip_address)
    ).scalar_one_or_none()
    if state is None:
        state = LoginThrottle(ip_address=ip_address)
        db.add(state)
        db.flush()
    return state


def _reset_state(state: LoginThrottle) -> None:
    state.failed_attempts = 0
    state.last_failed_at = None
    state.is_permanent_blocked = False
    state.blocked_until = None


def get_login_throttle_status(ip_address: str) -> LoginThrottleStatus:
    if is_explicitly_blocked_ip(ip_address):
        return LoginThrottleStatus(
            ip_address=ip_address,
            failed_attempts=WEB_AUTH_MAX_FAILURES,
            is_permanent_blocked=True,
        )

    now = datetime.utcnow()
    with session_scope() as db:
        state = db.execute(
            select(LoginThrottle).where(LoginThrottle.ip_address == ip_address)
        ).scalar_one_or_none()
        if state is None:
            return LoginThrottleStatus(ip_address=ip_address)

        if state.blocked_until and state.blocked_until <= now:
            _reset_state(state)
            return LoginThrottleStatus(ip_address=ip_address)

        return LoginThrottleStatus(
            ip_address=ip_address,
            failed_attempts=int(state.failed_attempts or 0),
            is_permanent_blocked=bool(state.is_permanent_blocked),
            blocked_until=state.blocked_until,
        )


def register_failed_login(ip_address: str) -> LoginThrottleStatus:
    if is_explicitly_blocked_ip(ip_address):
        return LoginThrottleStatus(
            ip_address=ip_address,
            failed_attempts=WEB_AUTH_MAX_FAILURES,
            is_permanent_blocked=True,
        )

    now = datetime.utcnow()
    with session_scope() as db:
        state = _get_or_create_throttle(db, ip_address)
        if state.is_permanent_blocked:
            return LoginThrottleStatus(
                ip_address=ip_address,
                failed_attempts=int(state.failed_attempts or WEB_AUTH_MAX_FAILURES),
                is_permanent_blocked=True,
            )
        if state.blocked_until and state.blocked_until <= now:
            _reset_state(state)

        state.failed_attempts = int(state.failed_attempts or 0) + 1
        state.last_failed_at = now

        if state.failed_attempts >= WEB_AUTH_MAX_FAILURES:
            if WEB_AUTH_PERMANENT_BLOCK_AFTER_FAILURES:
                state.is_permanent_blocked = True
                state.blocked_until = None
                logger.warning(
                    "Permanently blocked login IP %s after %s failed attempts",
                    ip_address,
                    state.failed_attempts,
                )
            else:
                state.blocked_until = now + timedelta(minutes=WEB_AUTH_LOCKOUT_MINUTES)
                logger.warning(
                    "Blocked login IP %s after %s failed attempts until %s",
                    ip_address,
                    state.failed_attempts,
                    state.blocked_until.isoformat(timespec="seconds"),
                )

        return LoginThrottleStatus(
            ip_address=ip_address,
            failed_attempts=int(state.failed_attempts or 0),
            is_permanent_blocked=bool(state.is_permanent_blocked),
            blocked_until=state.blocked_until,
        )


def clear_failed_logins(ip_address: str) -> None:
    if is_explicitly_blocked_ip(ip_address):
        return
    with session_scope() as db:
        state = db.execute(
            select(LoginThrottle).where(LoginThrottle.ip_address == ip_address)
        ).scalar_one_or_none()
        if state is None:
            return
        if state.failed_attempts or state.last_failed_at or state.blocked_until:
            _reset_state(state)


def safe_next_url(next_url: str | None, fallback: str = "/messages/") -> str:
    """Allow only same-origin relative redirects."""
    if not next_url:
        return fallback
    parsed = urlparse(next_url)
    if parsed.scheme or parsed.netloc:
        return fallback
    if not next_url.startswith("/"):
        return fallback
    return next_url


async def require_app_password(request: Request, call_next):
    """Redirect unauthenticated users to the login page when enabled."""
    if not WEB_AUTH_ENABLED or is_public_path(request.url.path) or is_authenticated(request):
        return await call_next(request)

    next_url = request.url.path
    if request.url.query:
        next_url = f"{next_url}?{request.url.query}"

    login_url = str(request.url_for("auth.login_form"))
    return RedirectResponse(
        url=f"{login_url}?{urlencode({'next': next_url})}",
        status_code=303,
    )


def password_matches(candidate: str, expected: str) -> bool:
    return bool(candidate) and bool(expected) and secrets.compare_digest(candidate, expected)
