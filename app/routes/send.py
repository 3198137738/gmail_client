"""Routes for composing and sending email via SMTP."""
from __future__ import annotations

import logging
import shutil
import uuid
from datetime import datetime
from email.utils import getaddresses
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from ..config import ATTACHMENTS_DIR, SYNC_SENT_ENABLED
from ..database import get_db
from ..models import Account, Attachment, Message, SentMessage
from ..services.smtp_service import SMTPSendError, send_email
from ..templating import templates, utc_to_local

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/compose", tags=["compose"])

OUTBOX_DIR = ATTACHMENTS_DIR / "outbox"
OUTBOX_DIR.mkdir(parents=True, exist_ok=True)

# 发件记录页最多显示的条数。
SENT_LIST_LIMIT = 200


def _coerce_int(value) -> Optional[int]:
    """Parse an int from a form value, tolerating empty/invalid input."""
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _emails_from(value: Optional[str]) -> list[str]:
    """Extract bare email addresses from a header value, preserving order."""
    if not value:
        return []
    return [addr for _name, addr in getaddresses([value]) if addr]


def _reply_subject(subject: Optional[str]) -> str:
    base = (subject or "").strip()
    if base.lower().startswith("re:"):
        return base
    return f"Re: {base}" if base else "Re:"


def _quote_body(message: Message) -> str:
    """Build a quoted version of the original message for the reply body."""
    source = message.body_text or ""
    if not source and message.body_html:
        # Crude fallback when only HTML exists: strip tags so the quote is
        # at least readable plain text.
        import re

        source = re.sub(r"<[^>]+>", "", message.body_html)
    quoted = "\n".join(f"> {line}" for line in source.splitlines())
    when = ""
    if message.date:
        try:
            when = message.date.strftime("%Y-%m-%d %H:%M")
        except Exception:
            when = ""
    sender = message.from_addr or ""
    header = f"在 {when}，{sender} 写道：" if when else f"{sender} 写道："
    return f"\n\n{header}\n{quoted}\n"


def _build_reply_prefill(message: Message, *, reply_all: bool) -> dict:
    """Compute prefilled compose fields for a (reply / reply-all) action."""
    sender_emails = _emails_from(message.from_addr)
    to = ", ".join(sender_emails)

    cc = ""
    if reply_all:
        own = (message.account.email or "").lower() if message.account else ""
        seen = {e.lower() for e in sender_emails}
        if own:
            seen.add(own)
        cc_emails: list[str] = []
        for addr in _emails_from(message.to_addr) + _emails_from(message.cc_addr):
            key = addr.lower()
            if key in seen:
                continue
            seen.add(key)
            cc_emails.append(addr)
        cc = ", ".join(cc_emails)

    return {
        "account_id": int(message.account_id),
        "to": to,
        "cc": cc,
        "bcc": "",
        "subject": _reply_subject(message.subject),
        "body_text": _quote_body(message),
        "in_reply_to": message.message_id or "",
        "references": message.message_id or "",
        "is_reply": True,
        "reply_all": reply_all,
    }


def _forward_subject(subject: Optional[str]) -> str:
    base = (subject or "").strip()
    low = base.lower()
    if low.startswith("fwd:") or low.startswith("fw:"):
        return base
    return f"Fwd: {base}" if base else "Fwd:"


def _forward_body(message: Message) -> str:
    """Build a forwarded body block with the original headers and content."""
    source = message.body_text or ""
    if not source and message.body_html:
        import re

        source = re.sub(r"<[^>]+>", "", message.body_html)
    when = ""
    if message.date:
        try:
            when = message.date.strftime("%Y-%m-%d %H:%M")
        except Exception:
            when = ""
    lines = [
        "",
        "",
        "---------- 转发邮件 ----------",
        f"发件人：{message.from_addr or ''}",
        f"日期：{when}",
        f"主题：{message.subject or ''}",
        f"收件人：{message.to_addr or ''}",
    ]
    if message.cc_addr:
        lines.append(f"抄送：{message.cc_addr}")
    lines.append("")
    lines.append(source)
    return "\n".join(lines)


def _build_forward_prefill(message: Message) -> dict:
    """Compute prefilled compose fields for a forward action."""
    fwd_attachments = [a for a in message.attachments if not a.is_inline]
    return {
        "account_id": int(message.account_id),
        "to": "",
        "cc": "",
        "bcc": "",
        "subject": _forward_subject(message.subject),
        "body_text": _forward_body(message),
        "in_reply_to": "",
        "references": "",
        "is_forward": True,
        "forward_message_id": int(message.id),
        "forward_attachments": [(a.display_name or a.filename) for a in fwd_attachments],
    }


@router.get("/", response_class=HTMLResponse, name="compose.form")
def compose_form(
    request: Request,
    db: Session = Depends(get_db),
    error: Optional[str] = None,
    reply_to: Optional[int] = None,
    mode: Optional[str] = None,
    forward: Optional[int] = None,
):
    accounts = db.execute(
        select(Account).where(Account.enabled.is_(True)).order_by(Account.email)
    ).scalars().all()

    prefill: Optional[dict] = None
    source_id = forward if forward is not None else reply_to
    if source_id is not None:
        message = db.execute(
            select(Message)
            .options(
                selectinload(Message.account),
                selectinload(Message.attachments),
            )
            .where(Message.id == source_id)
        ).scalar_one_or_none()
        if message is None:
            raise HTTPException(status_code=404, detail="Message not found")
        if forward is not None:
            prefill = _build_forward_prefill(message)
        else:
            prefill = _build_reply_prefill(message, reply_all=(mode == "all"))

    return templates.TemplateResponse(
        request,
        "compose.html",
        {"accounts": accounts, "error": error, "prefill": prefill},
    )


@router.post("/", name="compose.send")
async def send(
    request: Request,
    account_id: int = Form(...),
    to: str = Form(...),
    cc: Optional[str] = Form(None),
    bcc: Optional[str] = Form(None),
    subject: str = Form(""),
    body_text: Optional[str] = Form(None),
    body_html: Optional[str] = Form(None),
    use_ssl: Optional[str] = Form("on"),
    in_reply_to: Optional[str] = Form(None),
    references: Optional[str] = Form(None),
    forward_message_id: Optional[str] = Form(None),
    attachments: List[UploadFile] = File(default=[]),
    db: Session = Depends(get_db),
):
    account = db.get(Account, account_id)
    if account is None or not account.enabled:
        raise HTTPException(status_code=400, detail="Selected account is not available")

    if not (to or cc or bcc):
        raise HTTPException(status_code=400, detail="At least one recipient is required")

    saved_paths: list[Path] = []
    session_id = uuid.uuid4().hex
    session_dir = OUTBOX_DIR / session_id
    session_dir.mkdir(parents=True, exist_ok=True)

    try:
        for upload in attachments or []:
            if not upload or not upload.filename:
                continue
            safe_name = Path(upload.filename).name
            dest = session_dir / safe_name
            with dest.open("wb") as out:
                shutil.copyfileobj(upload.file, out)
            saved_paths.append(dest)

        # When forwarding, re-attach the original message's (non-inline)
        # attachments by copying them into this send session under their
        # original display names.
        fwd_id = _coerce_int(forward_message_id)
        if fwd_id:
            originals = db.execute(
                select(Attachment)
                .where(
                    Attachment.message_id == fwd_id,
                    Attachment.is_inline.is_(False),
                )
            ).scalars().all()
            for att in originals:
                src = Path(att.file_path)
                if not src.is_file():
                    logger.warning("Forwarded attachment missing on disk: %s", att.file_path)
                    continue
                copy_name = Path(att.display_name or att.filename).name or src.name
                dest = session_dir / copy_name
                n = 1
                while dest.exists():
                    dest = session_dir / f"{dest.stem}_{n}{dest.suffix}"
                    n += 1
                try:
                    shutil.copyfile(src, dest)
                except OSError:
                    logger.warning("Failed to copy forwarded attachment: %s", att.file_path)
                    continue
                saved_paths.append(dest)

        try:
            message_id = send_email(
                from_email=account.email,
                encrypted_password=account.encrypted_password,
                from_name=account.display_name,
                to=to,
                cc=cc,
                bcc=bcc,
                subject=subject,
                body_text=body_text,
                body_html=body_html,
                attachments=saved_paths,
                use_ssl=(use_ssl == "on"),
                in_reply_to=(in_reply_to or None),
                references=(references or None),
            )
            status = "sent"
            error = None
        except SMTPSendError as exc:
            status = "failed"
            error = str(exc)
            message_id = None
            logger.warning("SMTP send failed for account=%s: %s", account_id, exc)

        record = SentMessage(
            account_id=account_id,
            to_addr=to,
            cc_addr=cc,
            bcc_addr=bcc,
            subject=subject,
            body_text=body_text,
            body_html=body_html,
            message_id=message_id,
            status=status,
            error=error,
        )
        db.add(record)
        db.commit()

        if status == "failed":
            return RedirectResponse(
                url=request.url_for("compose.form").include_query_params(error=error),
                status_code=303,
            )
        return RedirectResponse(url=request.url_for("compose.sent_list"), status_code=303)
    finally:
        for p in saved_paths:
            try:
                p.unlink(missing_ok=True)
            except OSError:
                pass
        try:
            session_dir.rmdir()
        except OSError:
            pass


def _normalize_message_id(value: Optional[str]) -> str:
    return (value or "").strip().strip("<>").strip().lower()


@router.get("/sent", response_class=HTMLResponse, name="compose.sent_list")
def sent_list(request: Request, db: Session = Depends(get_db)):
    """发件记录：合并 Gmail「已发送」文件夹（需开启 SYNC_SENT_ENABLED）与本客户端的发送记录。

    本客户端发出的邮件 Gmail 也会存入「已发送」，按 Message-ID 去重后只显示 Gmail 那一份；
    发送失败或尚未同步到的本地记录照常显示。
    """
    gmail_rows = db.execute(
        select(Message)
        .options(selectinload(Message.account))
        .where(Message.folder_kind == "sent")
        .order_by(Message.date.desc().nulls_last(), Message.id.desc())
        .limit(SENT_LIST_LIMIT)
    ).scalars().all()
    local_rows = db.execute(
        select(SentMessage)
        .options(selectinload(SentMessage.account))
        .order_by(SentMessage.sent_at.desc())
        .limit(SENT_LIST_LIMIT)
    ).scalars().all()

    # 去重时不受上面 LIMIT 影响：直接查本地记录的 Message-ID 是否已同步到 Gmail「已发送」。
    local_ids = {_normalize_message_id(r.message_id) for r in local_rows if r.message_id}
    synced_ids: set[str] = set()
    if local_ids:
        candidates = list(local_ids) + [f"<{mid}>" for mid in local_ids]
        synced_ids = {
            _normalize_message_id(mid)
            for mid in db.execute(
                select(Message.message_id).where(
                    Message.folder_kind == "sent",
                    func.lower(Message.message_id).in_(candidates),
                )
            ).scalars().all()
        }

    items: list[dict] = []
    for m in gmail_rows:
        items.append({
            "source": "gmail",
            "account": m.account.email if m.account else f"#{m.account_id}",
            "to_addr": m.to_addr or "",
            "subject": m.subject,
            "time": m.date,
            "status": "sent",
            "error": None,
            "detail_url": request.url_for("messages.detail", message_id=m.id),
            "delete_url": request.url_for("messages.delete_one", message_id=m.id),
            "delete_confirm": "确定删除这封邮件吗？Gmail「已发送」中的邮件也会移到已删除邮件。",
        })
    for r in local_rows:
        if r.status == "sent" and _normalize_message_id(r.message_id) in synced_ids:
            continue
        items.append({
            "source": "local",
            "account": r.account.email if r.account else f"#{r.account_id}",
            "to_addr": r.to_addr or "",
            "subject": r.subject,
            "time": utc_to_local(r.sent_at),
            "status": r.status,
            "error": r.error,
            "detail_url": request.url_for("compose.sent_detail", sent_id=r.id),
            "delete_url": request.url_for("compose.sent_delete", sent_id=r.id),
            "delete_confirm": "确定删除这条发件记录吗？",
        })

    items.sort(key=lambda it: it["time"] or datetime.min, reverse=True)
    return templates.TemplateResponse(
        request,
        "sent.html",
        {"sent": items[:SENT_LIST_LIMIT], "sync_sent_enabled": SYNC_SENT_ENABLED},
    )


@router.get("/sent/{sent_id}", response_class=HTMLResponse, name="compose.sent_detail")
def sent_detail(request: Request, sent_id: int, db: Session = Depends(get_db)):
    record = db.execute(
        select(SentMessage)
        .options(selectinload(SentMessage.account))
        .where(SentMessage.id == sent_id)
    ).scalar_one_or_none()
    if record is None:
        raise HTTPException(status_code=404, detail="Sent message not found")
    return templates.TemplateResponse(
        request,
        "sent_detail.html",
        {"message": record},
    )


@router.post("/sent/{sent_id}/delete", name="compose.sent_delete")
def sent_delete(
    request: Request,
    sent_id: int,
    next: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    record = db.get(SentMessage, sent_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Sent message not found")
    db.delete(record)
    db.commit()
    logger.info("Deleted sent record id=%s account_id=%s", sent_id, record.account_id)
    if next:
        return RedirectResponse(url=next, status_code=303)
    return RedirectResponse(url=request.url_for("compose.sent_list"), status_code=303)
