"""登录设备管理：服务端登录会话。

会话 cookie 由 SessionMiddleware 签名，本身无法从服务端作废；所以登录时生成一个随机令牌
放进 cookie，数据库只存它的 SHA-256。每个请求都核对令牌对应的记录还在且没被撤销，
撤销记录即可让那台设备立即登出。

访问密码（WEB_AUTH_PASSWORD）在 .env 里，修改后要重启才生效：启动时比对密码指纹，
发现变化就撤销所有会话，旧设备必须用新密码重新登录。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import delete, or_, select, update

from ..config import SESSION_MAX_AGE_SEC, WEB_AUTH_PASSWORD
from ..database import session_scope
from ..models import AppSetting, LoginSession
from ..timeutil import utcnow

logger = logging.getLogger(__name__)

# 最近访问时间最多每隔这么久写一次库，避免每个请求都写
_TOUCH_INTERVAL = timedelta(seconds=60)
PASSWORD_FP_KEY = "web_auth_password_fp"


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("ascii", "ignore")).hexdigest()


def create(ip: Optional[str], user_agent: Optional[str]) -> str:
    """登记一次新的登录，返回放进 cookie 的令牌。"""
    token = secrets.token_urlsafe(32)
    now = utcnow()
    with session_scope() as db:
        db.add(
            LoginSession(
                token_hash=_hash(token),
                ip_address=(ip or None) and ip[:255],
                user_agent=(user_agent or None) and user_agent[:512],
                created_at=now,
                last_seen_at=now,
            )
        )
    return token


def validate(token: Optional[str], ip: Optional[str] = None) -> bool:
    """令牌有效：记录存在、未撤销、未超过登录有效期。顺带更新最近访问时间和 IP。"""
    if not token or not isinstance(token, str):
        return False
    now = utcnow()
    with session_scope() as db:
        row = db.execute(
            select(LoginSession).where(LoginSession.token_hash == _hash(token))
        ).scalar_one_or_none()
        if row is None or row.revoked_at is not None:
            return False
        if now - row.created_at > timedelta(seconds=SESSION_MAX_AGE_SEC):
            return False
        if now - row.last_seen_at >= _TOUCH_INTERVAL or (ip and ip != row.ip_address):
            row.last_seen_at = now
            if ip:
                row.ip_address = ip[:255]
    return True


def revoke_token(token: Optional[str]) -> None:
    if not token or not isinstance(token, str):
        return
    with session_scope() as db:
        db.execute(
            update(LoginSession)
            .where(LoginSession.token_hash == _hash(token), LoginSession.revoked_at.is_(None))
            .values(revoked_at=utcnow())
        )


def revoke(session_id: int) -> bool:
    with session_scope() as db:
        result = db.execute(
            update(LoginSession)
            .where(LoginSession.id == session_id, LoginSession.revoked_at.is_(None))
            .values(revoked_at=utcnow())
        )
        return bool(result.rowcount)


def revoke_all(*, except_token: Optional[str] = None) -> int:
    """撤销所有有效会话（可保留当前这台），返回撤销数量。"""
    stmt = update(LoginSession).where(LoginSession.revoked_at.is_(None))
    if except_token:
        stmt = stmt.where(LoginSession.token_hash != _hash(except_token))
    with session_scope() as db:
        result = db.execute(stmt.values(revoked_at=utcnow()))
        return int(result.rowcount or 0)


def revoke_by_ip(ip: str) -> int:
    """撤销某个 IP 上的所有有效会话（拉黑 IP 时用），返回撤销数量。"""
    with session_scope() as db:
        result = db.execute(
            update(LoginSession)
            .where(LoginSession.ip_address == ip, LoginSession.revoked_at.is_(None))
            .values(revoked_at=utcnow())
        )
        return int(result.rowcount or 0)


@dataclass
class SessionView:
    id: int
    ip_address: Optional[str]
    device: str
    created_at: datetime
    last_seen_at: datetime
    is_current: bool


def list_active(current_token: Optional[str] = None) -> list[SessionView]:
    cutoff = utcnow() - timedelta(seconds=SESSION_MAX_AGE_SEC)
    current_hash = _hash(current_token) if isinstance(current_token, str) and current_token else None
    with session_scope() as db:
        rows = db.execute(
            select(LoginSession)
            .where(LoginSession.revoked_at.is_(None), LoginSession.created_at > cutoff)
            .order_by(LoginSession.last_seen_at.desc())
        ).scalars().all()
        return [
            SessionView(
                id=int(r.id),
                ip_address=r.ip_address,
                device=describe_user_agent(r.user_agent),
                created_at=r.created_at,
                last_seen_at=r.last_seen_at,
                is_current=current_hash is not None and r.token_hash == current_hash,
            )
            for r in rows
        ]


def prune(days: int = 30) -> None:
    """删除早已撤销或过期的记录。"""
    cutoff = utcnow() - timedelta(days=days)
    expired = utcnow() - timedelta(seconds=SESSION_MAX_AGE_SEC) - timedelta(days=days)
    with session_scope() as db:
        db.execute(
            delete(LoginSession).where(
                or_(LoginSession.revoked_at < cutoff, LoginSession.created_at < expired)
            )
        )


# ---- 访问密码变化检测 ----------------------------------------------------------
def _fingerprint(password: str, salt: bytes) -> str:
    # 存在数据库里，用 scrypt 而不是快速哈希，数据库泄露也难以反推访问密码
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return base64.b64encode(salt).decode() + "$" + base64.b64encode(digest).decode()


def _fingerprint_matches(password: str, stored: str) -> bool:
    try:
        salt_b64, _sep, _digest = stored.partition("$")
        expected = _fingerprint(password, base64.b64decode(salt_b64))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(expected, stored)


def check_password_change(password: str = WEB_AUTH_PASSWORD) -> bool:
    """启动时调用：访问密码与上次启动时不同则撤销所有会话。返回是否发生了变化。"""
    if not password:
        return False
    with session_scope() as db:
        row = db.get(AppSetting, PASSWORD_FP_KEY)
        if row is not None and _fingerprint_matches(password, row.value):
            return False
        fp = _fingerprint(password, secrets.token_bytes(16))
        first_time = row is None
        if row is None:
            db.add(AppSetting(key=PASSWORD_FP_KEY, value=fp))
        else:
            row.value = fp
    if first_time:
        return False
    revoked = revoke_all()
    logger.warning("Web access password changed; revoked %d login session(s)", revoked)
    from . import audit

    audit.record("password_changed", f"已登出 {revoked} 台设备", source="system")
    audit.send_alert(
        "\U0001F511 <b>邮箱客户端的访问密码已修改</b>\n"
        f"所有已登录的设备（{revoked} 台）已自动登出。如果不是你本人修改的，请立即检查服务器。"
    )
    return True


# ---- User-Agent 简单识别 -------------------------------------------------------
_BROWSERS = (
    ("Edge", re.compile(r"Edg(?:e|A|iOS)?/")),
    ("Opera", re.compile(r"OPR/|Opera")),
    ("Firefox", re.compile(r"Firefox/|FxiOS/")),
    ("Chrome", re.compile(r"Chrome/|CriOS/")),
    ("Safari", re.compile(r"Safari/")),
)
_SYSTEMS = (
    ("iPhone", re.compile(r"iPhone")),
    ("iPad", re.compile(r"iPad")),
    ("Android", re.compile(r"Android")),
    ("Windows", re.compile(r"Windows")),
    ("macOS", re.compile(r"Mac OS X|Macintosh")),
    ("Linux", re.compile(r"Linux")),
)


def describe_user_agent(user_agent: Optional[str]) -> str:
    ua = user_agent or ""
    browser = next((name for name, pat in _BROWSERS if pat.search(ua)), None)
    system = next((name for name, pat in _SYSTEMS if pat.search(ua)), None)
    if browser and system:
        return f"{browser} · {system}"
    if browser or system:
        return browser or system  # type: ignore[return-value]
    return (ua[:60] or "未知设备")
