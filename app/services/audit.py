"""审计日志与异常告警。

记录登录、删信、发信、账号变更等操作；写日志失败只记到 app.log，绝不影响业务本身。
从没登录成功过的 IP 登录成功、IP 被封禁、访问密码被修改时，用 Telegram 提醒。
"""
from __future__ import annotations

import logging
import threading
from datetime import timedelta
from html import escape
from typing import Optional

from sqlalchemy import delete, func, select

from ..config import AUDIT_ALERT_IP_BLOCKED, AUDIT_ALERT_NEW_IP, AUDIT_RETENTION_DAYS
from ..database import session_scope
from ..models import AuditLog
from ..timeutil import utcnow

logger = logging.getLogger(__name__)

# 事件名 → 页面上显示的中文
EVENT_LABELS = {
    "login_success": "登录成功",
    "login_failed": "密码错误",
    "login_2fa_failed": "验证码错误",
    "ip_blocked": "IP 被封禁",
    "ip_banned": "拉黑 IP",
    "ip_unbanned": "解除 IP 封禁",
    "logout": "退出登录",
    "session_revoked": "登出设备",
    "sessions_revoked_all": "全部登出",
    "password_changed": "访问密码已修改",
    "2fa_enabled": "开启两步验证",
    "2fa_disabled": "关闭两步验证",
    "mail_sent": "发送邮件",
    "mail_send_failed": "发送失败",
    "mail_deleted": "删除邮件",
    "sender_purged": "永久删除发件人邮件",
    "sent_record_deleted": "删除发件记录",
    "account_added": "添加账号",
    "account_updated": "修改账号",
    "account_deleted": "删除账号",
    "account_enabled": "启用账号",
    "account_disabled": "停用账号",
    "backup_created": "备份完成",
    "backup_failed": "备份失败",
    "backup_uploaded": "异地备份完成",
    "backup_upload_failed": "异地备份失败",
}

# 需要在页面上突出显示的事件
WARN_EVENTS = {
    "login_failed", "login_2fa_failed", "ip_blocked", "ip_banned", "password_changed",
    "2fa_disabled", "mail_send_failed", "backup_failed", "backup_upload_failed",
    "sessions_revoked_all",
}

SOURCE_LABELS = {"web": "网页", "telegram": "Telegram", "system": "系统"}

_DETAIL_MAX = 2000


def record(
    event: str,
    detail: Optional[str] = None,
    *,
    ip: Optional[str] = None,
    source: str = "web",
) -> None:
    """写一条审计日志（自带事务，失败不抛异常）。"""
    try:
        with session_scope() as db:
            db.add(
                AuditLog(
                    event=event[:48],
                    source=source[:16],
                    ip_address=(ip or None) and ip[:255],
                    detail=(detail or None) and detail[:_DETAIL_MAX],
                )
            )
    except Exception:
        logger.exception("Failed to write audit log event=%s", event)


def request_ip(request) -> Optional[str]:
    from ..auth import get_client_ip

    try:
        return get_client_ip(request)
    except Exception:
        return None


def record_request(request, event: str, detail: Optional[str] = None) -> None:
    """网页操作：自动带上客户端 IP。"""
    record(event, detail, ip=request_ip(request), source="web")


def summarize_subjects(subjects: list[Optional[str]], limit: int = 5) -> str:
    names = [(s or "(无主题)").strip()[:80] for s in subjects[:limit]]
    text = "；".join(f"「{n}」" for n in names)
    if len(subjects) > limit:
        text += f" 等 {len(subjects)} 封"
    return text


def recent(*, limit: int = 50, offset: int = 0) -> tuple[list[AuditLog], int]:
    with session_scope() as db:
        total = int(db.execute(select(func.count(AuditLog.id))).scalar_one())
        rows = db.execute(
            select(AuditLog).order_by(AuditLog.id.desc()).limit(limit).offset(offset)
        ).scalars().all()
        db.expunge_all()
        return list(rows), total


def is_known_login_ip(ip: str) -> bool:
    """该 IP 以前是否登录成功过（在保留期内）。"""
    with session_scope() as db:
        found = db.execute(
            select(AuditLog.id)
            .where(AuditLog.event == "login_success", AuditLog.ip_address == ip)
            .limit(1)
        ).first()
    return found is not None


def count_events(events: str | tuple[str, ...], *, since) -> int:
    names = (events,) if isinstance(events, str) else tuple(events)
    with session_scope() as db:
        return int(db.execute(
            select(func.count(AuditLog.id)).where(AuditLog.event.in_(names), AuditLog.created_at >= since)
        ).scalar_one())


def login_success_ips() -> set[str]:
    """保留期内登录成功过的全部 IP。"""
    with session_scope() as db:
        return {
            ip for ip in db.execute(
                select(AuditLog.ip_address).where(AuditLog.event == "login_success").distinct()
            ).scalars() if ip
        }


def prune(days: int = AUDIT_RETENTION_DAYS) -> int:
    cutoff = utcnow() - timedelta(days=days)
    with session_scope() as db:
        result = db.execute(delete(AuditLog).where(AuditLog.created_at < cutoff))
        return int(result.rowcount or 0)


def safe_prune() -> None:
    try:
        removed = prune()
        if removed:
            logger.info("Pruned %d audit log rows", removed)
    except Exception:
        logger.exception("Audit log prune failed")


# ---- Telegram 告警 -----------------------------------------------------------
def _send_alert_now(text: str) -> None:
    from . import telegram_service

    if not telegram_service.is_configured():
        return
    ok, detail = telegram_service.send_message(text)
    if not ok:
        logger.warning("Security alert via Telegram failed: %s", detail)


def send_alert(text: str) -> None:
    """后台发送 Telegram 告警，不阻塞请求。text 为 Telegram HTML。"""
    threading.Thread(target=_send_alert_now, args=(text,), name="security-alert", daemon=True).start()


def _ua_text(user_agent: Optional[str]) -> str:
    from .login_sessions import describe_user_agent

    return describe_user_agent(user_agent)


def on_login_success(ip: str, user_agent: Optional[str]) -> None:
    """登录成功：先判断是否新 IP，再写日志（顺序不能反）。"""
    new_ip = bool(ip) and ip != "unknown" and not is_known_login_ip(ip)
    record("login_success", _ua_text(user_agent) + ("（新 IP）" if new_ip else ""), ip=ip)
    if new_ip and AUDIT_ALERT_NEW_IP:
        send_alert(
            "⚠️ <b>新 IP 登录了邮箱客户端</b>\n"
            f"IP：<code>{escape(ip)}</code>\n"
            f"设备：{escape(_ua_text(user_agent))}\n"
            "如果不是你本人，请立即修改访问密码（改后所有设备自动登出），"
            "并在「设置 › 安全」查看登录设备。"
        )


def on_ip_blocked(ip: str, permanent: bool) -> None:
    record("ip_blocked", "永久封禁" if permanent else "临时封禁", ip=ip)
    if AUDIT_ALERT_IP_BLOCKED:
        send_alert(
            "\U0001F6AB <b>登录密码多次错误，IP 已被"
            + ("永久" if permanent else "临时")
            + f"封禁</b>\nIP：<code>{escape(ip)}</code>"
        )
