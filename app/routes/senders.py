"""发件人管理接口：屏蔽 / 解除屏蔽 / 永久删除、信任（这不是垃圾邮件）/ 取消信任。"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from .. import serializers as ser
from ..database import get_db
from ..models import BlockedSender, Message, TrustedSender
from ..services import audit
from ..services.background import submit_bg
from ..services.imap_purge import describe_purge, purge_sender_messages
from ..services.imap_restore import restore_sender_messages_since
from ..services.message_actions import (
    delete_local_messages as _delete_local_messages,
    mark_not_spam,
)
from ..timeutil import utcnow
from .messages import _coerce_int, _normalize_sender_email

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["senders"])


@router.post("/messages/{message_id}/not-spam", name="messages.not_spam")
def not_spam(message_id: int, db: Session = Depends(get_db)):
    """「这不是垃圾邮件」：信任发件人，并把其垃圾邮件移回收件箱。"""
    if db.get(Message, message_id) is None:
        raise HTTPException(status_code=404, detail="Message not found")
    db.close()  # 释放连接；后续操作在 mark_not_spam 内自行开启会话

    ok, info = mark_not_spam(message_id)
    if not ok:
        raise HTTPException(status_code=502, detail=info)
    return {"ok": True, "message": info}


@router.post("/messages/{message_id}/block-sender", name="messages.block_sender")
def block_sender(message_id: int, db: Session = Depends(get_db)):
    """永久屏蔽该账号下的这个发件人，并隐藏已有的同发件人邮件。"""
    message = db.get(Message, message_id)
    if message is None:
        raise HTTPException(status_code=404, detail="Message not found")

    sender_email = _normalize_sender_email(message.from_addr)
    if not sender_email:
        raise HTTPException(status_code=400, detail="Cannot parse sender email")

    exists = db.execute(
        select(BlockedSender.id).where(
            BlockedSender.account_id == message.account_id,
            BlockedSender.sender_email == sender_email,
        )
    ).first()
    if exists is None:
        db.add(BlockedSender(account_id=int(message.account_id), sender_email=sender_email))
        db.flush()

    pattern = f"%{sender_email}%"
    matched = db.execute(
        select(Message)
        .options(selectinload(Message.attachments))
        .where(
            Message.account_id == message.account_id,
            Message.from_addr.ilike(pattern),
        )
    ).scalars().all()
    to_delete = [m for m in matched if _normalize_sender_email(m.from_addr) == sender_email]
    deleted = _delete_local_messages(db, to_delete)
    logger.info(
        "block-sender account_id=%s sender=%s hidden=%s", message.account_id, sender_email, deleted,
    )
    return {"ok": True, "sender_email": sender_email, "hidden": deleted}


def _sender_row(row) -> dict:
    account = row.account
    return {
        "id": int(row.id),
        "account_id": int(row.account_id),
        "account_name": (account.display_name or account.email) if account else f"#{row.account_id}",
        "sender_email": row.sender_email,
        "created_at": ser.iso_utc(row.created_at),
    }


@router.get("/senders", name="senders.list")
def senders(
    account_id: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    account_id_int = _coerce_int(account_id)
    q_clean = (q or "").strip().lower()

    def rows(model):
        stmt = select(model).options(selectinload(model.account))
        if account_id_int:
            stmt = stmt.where(model.account_id == account_id_int)
        if q_clean:
            stmt = stmt.where(model.sender_email.ilike(f"%{q_clean}%"))
        return db.execute(stmt.order_by(model.created_at.desc(), model.id.desc())).scalars().all()

    return {
        "blocked": [_sender_row(r) for r in rows(BlockedSender)],
        "trusted": [_sender_row(r) for r in rows(TrustedSender)],
    }


@router.post("/senders/blocked/{blocked_id}/purge", name="senders.purge_blocked")
def purge_blocked_sender(request: Request, blocked_id: int, db: Session = Depends(get_db)):
    """「永久删除」：屏蔽保持不变，把该发件人在 Gmail 上的全部邮件彻底清除。"""
    row = db.execute(
        select(BlockedSender).options(selectinload(BlockedSender.account)).where(BlockedSender.id == blocked_id)
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Blocked sender not found")
    account_id = int(row.account_id)
    sender_email = row.sender_email
    account_email = row.account.email if row.account else f"#{account_id}"
    db.close()  # 释放连接；IMAP 操作可能要跑一会儿

    result = purge_sender_messages(account_id, sender_email)
    summary = describe_purge(result)
    if result["folders"]:
        # 已经动过 Gmail 里的邮件才记审计（连不上 / 账号忙时什么都没发生）
        audit.record_request(request, "sender_purged", f"{sender_email}（{account_email}）：{summary}")
    logger.info(
        "purge-blocked-sender account_id=%s sender=%s trashed=%s purged=%s local=%s ok=%s error=%s",
        account_id, sender_email, result["trashed"], result["purged"], result["local"],
        result["ok"], result["error"],
    )
    return {"ok": bool(result["ok"]), "message": f"{sender_email}：{summary}"}


@router.post("/senders/blocked/{blocked_id}/remove", name="senders.unblock")
def unblock_sender(blocked_id: int, db: Session = Depends(get_db)):
    """解除屏蔽，并在后台补回屏蔽期间被忽略的邮件。"""
    row = db.get(BlockedSender, blocked_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Blocked sender not found")
    account_id = int(row.account_id)
    sender_email = row.sender_email
    blocked_at = row.created_at or utcnow()
    db.delete(row)
    db.commit()
    submit_bg(restore_sender_messages_since, account_id, sender_email, blocked_at)
    logger.info(
        "unblock-sender account_id=%s sender=%s restore_since=%s",
        account_id, sender_email, blocked_at.isoformat(),
    )
    return {"ok": True}


@router.post("/senders/trusted/{trusted_id}/remove", name="senders.untrust")
def untrust_sender(trusted_id: int, db: Session = Depends(get_db)):
    """取消信任发件人：之后其邮件若被 Gmail 判为垃圾邮件，将正常留在垃圾邮件中。"""
    row = db.get(TrustedSender, trusted_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Trusted sender not found")
    logger.info("untrust-sender account_id=%s sender=%s", row.account_id, row.sender_email)
    db.delete(row)
    db.commit()
    return {"ok": True}
