"""发件人管理：屏蔽 / 解除屏蔽、信任（这不是垃圾邮件）/ 取消信任。

URL 仍在 /messages 下。注意本路由器必须先于 messages 路由器注册，
否则 GET /messages/blocked-senders 会被 /messages/{message_id} 抢先匹配。
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..auth import is_safe_redirect
from ..database import get_db
from ..models import Account, BlockedSender, Message, TrustedSender
from ..services.background import submit_bg
from ..services.imap_restore import restore_sender_messages_since
from ..services.message_actions import (
    delete_local_messages as _delete_local_messages,
    mark_not_spam,
)
from ..templating import templates
from ..timeutil import utcnow
from .messages import (
    DEFAULT_PAGE_SIZE,
    _coerce_int,
    _normalize_sender_email,
    _redirect_to_list,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/messages", tags=["senders"])


@router.post("/{message_id}/not-spam", name="messages.not_spam")
def not_spam(
    request: Request,
    message_id: int,
    next: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    """「这不是垃圾邮件」：信任发件人，并把其垃圾邮件移回收件箱。"""
    message = db.get(Message, message_id)
    if message is None:
        raise HTTPException(status_code=404, detail="Message not found")
    account_id = int(message.account_id)
    db.close()  # 释放连接；后续操作在 mark_not_spam 内自行开启会话

    ok, info = mark_not_spam(message_id)
    if not ok:
        raise HTTPException(status_code=502, detail=info)
    if is_safe_redirect(next):
        return RedirectResponse(url=next, status_code=303)
    return _redirect_to_list(
        request,
        kind="junk",
        account_id=account_id,
        q="",
        page=1,
        page_size=DEFAULT_PAGE_SIZE,
    )


@router.post("/{message_id}/block-sender", name="messages.block_sender")
def block_sender(
    request: Request,
    message_id: int,
    next: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    """Permanently block the sender for this account and hide existing mail."""
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
        db.add(
            BlockedSender(
                account_id=int(message.account_id),
                sender_email=sender_email,
            )
        )
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
        "block-sender account_id=%s sender=%s hidden=%s",
        message.account_id,
        sender_email,
        deleted,
    )

    if is_safe_redirect(next):
        return RedirectResponse(url=next, status_code=303)
    return _redirect_to_list(
        request,
        kind="inbox",
        account_id=int(message.account_id),
        q="",
        page=1,
        page_size=DEFAULT_PAGE_SIZE,
    )


@router.get("/blocked-senders", response_class=HTMLResponse, name="messages.blocked_senders")
def blocked_senders(
    request: Request,
    account_id: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    account_id_int = _coerce_int(account_id)
    q_clean = (q or "").strip().lower()
    accounts = db.execute(select(Account).order_by(Account.email)).scalars().all()

    stmt = select(BlockedSender).options(selectinload(BlockedSender.account))
    if account_id_int:
        stmt = stmt.where(BlockedSender.account_id == account_id_int)
    if q_clean:
        stmt = stmt.where(BlockedSender.sender_email.ilike(f"%{q_clean}%"))
    blocked_rows = db.execute(
        stmt.order_by(BlockedSender.created_at.desc(), BlockedSender.id.desc())
    ).scalars().all()

    trusted_stmt = select(TrustedSender).options(selectinload(TrustedSender.account))
    if account_id_int:
        trusted_stmt = trusted_stmt.where(TrustedSender.account_id == account_id_int)
    if q_clean:
        trusted_stmt = trusted_stmt.where(TrustedSender.sender_email.ilike(f"%{q_clean}%"))
    trusted_rows = db.execute(
        trusted_stmt.order_by(TrustedSender.created_at.desc(), TrustedSender.id.desc())
    ).scalars().all()

    return templates.TemplateResponse(
        request,
        "blocked_senders.html",
        {
            "accounts": accounts,
            "blocked_rows": blocked_rows,
            "trusted_rows": trusted_rows,
            "current_account_id": account_id_int,
            "q": q,
        },
    )


@router.post("/blocked-senders/{blocked_id}/remove", name="messages.unblock_sender")
def unblock_sender(
    request: Request,
    blocked_id: int,
    next: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    """Remove one blocked sender row from the permanent block list."""
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
        "unblock-sender account_id=%s sender=%s removed=%s restore_since=%s",
        account_id,
        sender_email,
        1,
        blocked_at.isoformat(),
    )
    if is_safe_redirect(next):
        return RedirectResponse(url=next, status_code=303)
    return RedirectResponse(
        url=request.url_for("messages.blocked_senders").include_query_params(account_id=account_id),
        status_code=303,
    )


@router.post("/trusted-senders/{trusted_id}/remove", name="messages.untrust_sender")
def untrust_sender(
    request: Request,
    trusted_id: int,
    next: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    """取消信任发件人：之后其邮件若被 Gmail 判为垃圾邮件，将正常留在垃圾邮件中。"""
    row = db.get(TrustedSender, trusted_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Trusted sender not found")
    account_id = int(row.account_id)
    logger.info("untrust-sender account_id=%s sender=%s", account_id, row.sender_email)
    db.delete(row)
    db.commit()
    if is_safe_redirect(next):
        return RedirectResponse(url=next, status_code=303)
    return RedirectResponse(
        url=request.url_for("messages.blocked_senders").include_query_params(account_id=account_id),
        status_code=303,
    )
