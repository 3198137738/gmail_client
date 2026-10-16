"""发件人管理接口：屏蔽 / 解除屏蔽 / 永久删除、信任（这不是垃圾邮件）/ 取消信任。

屏蔽发件人时，会在后台把该发件人在 Gmail 中的已有邮件移到已删除邮件（进度见右下角卡片），
之后同步收到的新邮件也会自动移过去（见 imap_service._trash_blocked），都显示在左侧「已删除」里。
"""
from __future__ import annotations

import logging
import re
from collections import defaultdict
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from .. import serializers as ser
from ..database import get_db
from ..models import Account, BlockedSender, Message, TrustedSender
from ..services import audit, purge_jobs
from ..services.background import submit_bg
from ..services.imap_purge import describe_purge, purge_sender_messages
from ..services.imap_restore import restore_sender_messages_since
from ..services.message_actions import (
    delete_local_messages as _delete_local_messages,
    mark_not_spam,
)
from ..timeutil import utcnow
from .messages import _coerce_int, _load_messages_by_ids, _normalize_sender_email

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


_EMAIL_RE = re.compile(r"[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}", re.IGNORECASE)


def _block_senders(db: Session, account_id: int, emails: set[str]) -> tuple[list[str], int]:
    """屏蔽该账号下的这些发件人，并立即删除本地已有的同发件人邮件（「已删除」里的保留）。

    返回（新增的屏蔽地址, 删除的本地邮件数）。Gmail 里的邮件由调用方用 :func:`_start_purge`
    在后台移到已删除邮件。
    """
    existing = set(db.execute(
        select(BlockedSender.sender_email).where(
            BlockedSender.account_id == account_id,
            BlockedSender.sender_email.in_(emails),
        )
    ).scalars())
    added = sorted(emails - existing)
    for email in added:
        db.add(BlockedSender(account_id=account_id, sender_email=email))
    db.flush()

    to_delete: list[Message] = []
    for email in sorted(emails):
        matched = db.execute(
            select(Message)
            .options(selectinload(Message.attachments))
            .where(
                Message.account_id == account_id,
                Message.folder_kind != "trash",
                Message.from_addr.ilike(f"%{email}%"),
            )
        ).scalars().all()
        to_delete.extend(m for m in matched if _normalize_sender_email(m.from_addr) == email)
    hidden = _delete_local_messages(db, to_delete)
    db.commit()
    logger.info(
        "block-senders account_id=%s senders=%s added=%s hidden=%s",
        account_id, len(emails), len(added), hidden,
    )
    return added, hidden


def _start_purge(
    request: Request, db: Session, by_account: dict[int, list[str]], *, permanent: bool
) -> Optional[dict]:
    """在后台删除这些发件人在 Gmail 中的全部邮件，返回进度任务快照（没有地址时返回 None）。

    ``permanent=False``：只移到已删除邮件（屏蔽时）。
    """
    accounts = {
        int(a.id): a.email
        for a in db.execute(select(Account).where(Account.id.in_(list(by_account)))).scalars()
    } if by_account else {}
    targets = [
        (account_id, email, accounts.get(account_id) or f"#{account_id}")
        for account_id, emails in sorted(by_account.items())
        for email in emails
    ]
    if not targets:
        return None
    job = purge_jobs.create(targets, permanent=permanent)
    submit_bg(purge_jobs.run, job["id"], targets, audit.request_ip(request), permanent=permanent)
    return job


@router.post("/messages/{message_id}/block-sender", name="messages.block_sender")
def block_sender(request: Request, message_id: int, db: Session = Depends(get_db)):
    """永久屏蔽该账号下的这个发件人，并把已有的同发件人邮件移到已删除（Gmail 中也移过去）。"""
    message = db.get(Message, message_id)
    if message is None:
        raise HTTPException(status_code=404, detail="Message not found")

    sender_email = _normalize_sender_email(message.from_addr)
    if not sender_email:
        raise HTTPException(status_code=400, detail="Cannot parse sender email")

    account_id = int(message.account_id)
    added, hidden = _block_senders(db, account_id, {sender_email})
    job = _start_purge(request, db, {account_id: added}, permanent=False)
    return {"ok": True, "sender_email": sender_email, "hidden": hidden, "job": job}


class BlockMessagesBody(BaseModel):
    ids: list[int] = []


@router.post("/senders/block", name="senders.block_messages")
def block_message_senders(request: Request, body: BlockMessagesBody, db: Session = Depends(get_db)):
    """批量屏蔽：屏蔽选中邮件的发件人（各自所在账号下），并把这些发件人已有的邮件移到已删除。

    账号自己的地址（如已发送邮件）会被跳过，避免把自己屏蔽掉。
    """
    messages = _load_messages_by_ids(db, body.ids)
    own = {
        int(a.id): (a.email or "").strip().lower()
        for a in db.execute(select(Account)).scalars()
    }
    by_account: dict[int, set[str]] = defaultdict(set)
    skipped = 0
    for message in messages:
        account_id = int(message.account_id)
        email = _normalize_sender_email(message.from_addr)
        if not email or email == own.get(account_id):
            skipped += 1
            continue
        by_account[account_id].add(email)

    senders: set[str] = set()
    hidden = 0
    new_by_account: dict[int, list[str]] = {}
    for account_id, emails in by_account.items():
        new, count = _block_senders(db, account_id, emails)
        senders |= emails
        hidden += count
        if new:
            new_by_account[account_id] = new
    job = _start_purge(request, db, new_by_account, permanent=False)
    added = sum(len(v) for v in new_by_account.values())
    return {
        "ok": True, "senders": sorted(senders), "added": added, "hidden": hidden,
        "skipped": skipped, "job": job,
    }


class BlockAddressesBody(BaseModel):
    account_id: int
    # 一次可粘贴多个地址：逗号、分号、空白或换行分隔，也可以是「名称 <地址>」
    text: str = ""


@router.post("/senders/blocked", name="senders.block_addresses")
def block_addresses(request: Request, body: BlockAddressesBody, db: Session = Depends(get_db)):
    """在设置页手动批量添加屏蔽地址，并把这些地址已有的邮件移到已删除。"""
    account = db.get(Account, body.account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")
    own = (account.email or "").strip().lower()
    emails = {e.lower() for e in _EMAIL_RE.findall(body.text or "")} - {own}
    if not emails:
        raise HTTPException(status_code=400, detail="Invalid email address")
    account_id = int(account.id)
    added, hidden = _block_senders(db, account_id, emails)
    job = _start_purge(request, db, {account_id: added}, permanent=False)
    return {
        "ok": True, "added": len(added), "existing": len(emails) - len(added),
        "hidden": hidden, "job": job,
    }


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


class BlockedIdsBody(BaseModel):
    ids: list[int] = []


@router.post("/senders/blocked/purge", name="senders.purge_blocked_many")
def purge_blocked_senders(request: Request, body: BlockedIdsBody, db: Session = Depends(get_db)):
    """批量「永久删除」：在后台依次清除选中屏蔽地址在 Gmail 中的全部邮件，屏蔽保持不变。

    多个地址逐个连 IMAP 可能要很久，放在请求里会超时；进度通过实时推送通知页面，也可用
    ``GET /api/senders/purge-jobs`` 查询，每个地址的结果另记审计日志。
    """
    ids = sorted({int(i) for i in body.ids if i})
    rows = db.execute(
        select(BlockedSender).options(selectinload(BlockedSender.account)).where(BlockedSender.id.in_(ids))
    ).scalars().all() if ids else []
    if not rows:
        raise HTTPException(status_code=404, detail="Blocked sender not found")
    by_account: dict[int, list[str]] = defaultdict(list)
    for r in rows:
        by_account[int(r.account_id)].append(r.sender_email)
    job = _start_purge(request, db, by_account, permanent=True)
    return {"ok": True, "queued": len(rows), "job": job}


@router.get("/senders/purge-jobs", name="senders.purge_jobs")
def purge_job_list():
    """进行中和最近结束（尚未关闭提示）的批量永久删除任务。"""
    return {"jobs": purge_jobs.list_jobs()}


@router.post("/senders/purge-jobs/{job_id}/dismiss", name="senders.dismiss_purge_job")
def dismiss_purge_job(job_id: str):
    """关闭已结束任务的结果提示。"""
    return {"ok": purge_jobs.dismiss(job_id)}


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
