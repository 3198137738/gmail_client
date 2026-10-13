"""批量清理：跨账号按发件人 / 时间 / 已读状态找出邮件，先预览数量再一次删除（同步到 Gmail）。

星标邮件和已延后的邮件始终保留，不会被清理。
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from .. import serializers as ser
from ..database import get_db
from ..models import Message
from ..services import audit, outbox
from ..services.message_actions import delete_local_messages
from ..services.search import _escape_like
from .messages import _enqueue_remote_delete, _kind_condition, _normalize_sender_email

router = APIRouter(prefix="/api/cleanup", tags=["cleanup"])

SCOPES = ("inbox", "all", "junk")
TOP_SENDERS = 100
DELETE_CHUNK = 500


class CleanupBody(BaseModel):
    scope: str = "inbox"
    account_id: Optional[int] = None
    older_than_days: int = 0
    only_read: bool = False
    senders: list[str] = []


def _conditions(body: CleanupBody) -> list:
    if body.scope not in SCOPES:
        raise HTTPException(status_code=400, detail="无效的范围")
    conditions = [
        _kind_condition(body.scope),
        Message.is_starred.is_(False),
        Message.snoozed_until.is_(None),
    ]
    if body.account_id:
        conditions.append(Message.account_id == body.account_id)
    if body.older_than_days > 0:
        conditions.append(Message.date < datetime.now() - timedelta(days=body.older_than_days))
    if body.only_read:
        conditions.append(Message.is_read.is_(True))
    return conditions


def _senders(body: CleanupBody) -> set[str]:
    return {s.strip().lower() for s in body.senders if s and s.strip()}


def _matching_ids(db: Session, body: CleanupBody) -> list[int]:
    stmt = select(Message.id, Message.from_addr).where(*_conditions(body))
    senders = _senders(body)
    if senders:
        # 先用 LIKE 缩小范围，再按解析出的地址精确比较（避免 a@x.com 匹配到 aa@x.com）
        stmt = stmt.where(or_(*(Message.from_addr.ilike(f"%{_escape_like(s)}%", escape="!") for s in senders)))
        return [mid for mid, from_addr in db.execute(stmt) if _normalize_sender_email(from_addr) in senders]
    return [mid for mid, _ in db.execute(stmt)]


def _require_filter(body: CleanupBody) -> None:
    """没选发件人也没限定时间时，只允许清空垃圾邮件，避免一键删光收件箱。"""
    if not _senders(body) and body.older_than_days <= 0 and body.scope != "junk":
        raise HTTPException(status_code=400, detail="请选择发件人或设置「早于」时间")


@router.post("/senders", name="cleanup.senders")
def top_senders(body: CleanupBody, db: Session = Depends(get_db)):
    """符合条件的邮件按发件人汇总，按数量从多到少。"""
    stats: dict[str, dict] = defaultdict(lambda: {"count": 0, "unread": 0, "latest": None, "name": ""})
    rows = db.execute(
        select(Message.from_addr, Message.is_read, Message.date).where(*_conditions(body))
    )
    total = 0
    for from_addr, is_read, date in rows:
        email = _normalize_sender_email(from_addr)
        if not email:
            continue
        total += 1
        item = stats[email]
        item["count"] += 1
        item["unread"] += 0 if is_read else 1
        if date and (item["latest"] is None or date > item["latest"]):
            item["latest"] = date
            item["name"] = ser.addr_name(from_addr)
    ranked = sorted(stats.items(), key=lambda kv: (-kv[1]["count"], kv[0]))[:TOP_SENDERS]
    return {
        "total": total,
        "senders": [
            {
                "email": email,
                "name": s["name"] if s["name"] != email else "",
                "count": s["count"],
                "unread": s["unread"],
                "latest": ser.iso(s["latest"]),
            }
            for email, s in ranked
        ],
    }


@router.post("/preview", name="cleanup.preview")
def preview(body: CleanupBody, db: Session = Depends(get_db)):
    _require_filter(body)
    ids = _matching_ids(db, body)
    samples = db.execute(
        select(Message).where(Message.id.in_(ids[:2000])).order_by(Message.date.desc()).limit(5)
    ).scalars().all() if ids else []
    return {
        "count": len(ids),
        "samples": [
            {"id": int(m.id), "subject": m.subject or "", "from": ser.addr_name(m.from_addr), "date": ser.iso(m.date)}
            for m in samples
        ],
    }


@router.post("/delete", name="cleanup.delete")
def delete(request: Request, body: CleanupBody, db: Session = Depends(get_db)):
    _require_filter(body)
    ids = _matching_ids(db, body)
    touched: set[int] = set()
    deleted = 0
    for start in range(0, len(ids), DELETE_CHUNK):
        messages = db.execute(
            select(Message).options(selectinload(Message.attachments))
            .where(Message.id.in_(ids[start:start + DELETE_CHUNK]))
        ).scalars().all()
        touched.update(_enqueue_remote_delete(db, messages))
        deleted += delete_local_messages(db, messages)
    if deleted:
        parts = [{"inbox": "收件箱", "all": "所有邮件", "junk": "垃圾邮件"}[body.scope]]
        if body.older_than_days > 0:
            parts.append(f"早于 {body.older_than_days} 天")
        if body.only_read:
            parts.append("仅已读")
        senders = sorted(_senders(body))
        if senders:
            parts.append("发件人 " + "、".join(senders[:5]) + (f" 等 {len(senders)} 个" if len(senders) > 5 else ""))
        audit.record_request(request, "mail_cleanup", f"{'，'.join(parts)}：{deleted} 封")
        outbox.kick(sorted(touched))
    return {"ok": True, "deleted": deleted}
