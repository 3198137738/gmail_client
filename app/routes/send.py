"""写邮件、草稿、发件记录的接口（SMTP 发信）。"""
from __future__ import annotations

import logging
import shutil
import uuid
from collections import Counter
from datetime import datetime
from html import escape
from email.utils import getaddresses
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from .. import serializers as ser
from ..config import ATTACHMENTS_DIR, SYNC_SENT_ENABLED
from ..database import get_db
from ..models import Account, Attachment, Draft, Message, SentMessage
from ..services.mail_body import (
    body_document_response,
    body_has_remote_images,
    remote_images_allowed,
    request_origin,
)
from ..services import audit
from ..services.attachment_restore import local_path, restore_message_files
from ..services.html_sanitizer import sanitize_compose_html
from ..services.smtp_service import SMTPSendError, send_email
from ..navigation import sidebar_data
from ..timeutil import utc_to_local

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["compose"])
# 不是 JSON 接口的部分：发件记录的正文文档（在详情页的 sandbox iframe 中加载）
body_router = APIRouter(prefix="/compose", tags=["compose"])

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


def _plain_source(message: Message) -> str:
    """原邮件的纯文本内容；只有 HTML 时粗略去掉标签。"""
    source = message.body_text or ""
    if not source and message.body_html:
        import re

        source = re.sub(r"<[^>]+>", "", message.body_html)
    return source


def _message_when(message: Message) -> str:
    if message.date:
        try:
            return message.date.strftime("%Y-%m-%d %H:%M")
        except Exception:
            return ""
    return ""


def _quote_header(message: Message) -> str:
    when = _message_when(message)
    sender = message.from_addr or ""
    return f"在 {when}，{sender} 写道：" if when else f"{sender} 写道："


def _text_to_html(text: str) -> str:
    return escape(text).replace("\n", "<br>")


def _quote_body(message: Message) -> str:
    """Build a quoted version of the original message for the reply body."""
    quoted = "\n".join(f"> {line}" for line in _plain_source(message).splitlines())
    return f"\n\n{_quote_header(message)}\n{quoted}\n"


def _quote_html(message: Message) -> str:
    """Gmail 风格的 HTML 回复引用：上方留空供输入，原文放在带左边线的引用块中。"""
    return (
        "<div><br></div><div><br></div>"
        '<div class="gmail_quote">'
        f"<div>{escape(_quote_header(message))}</div>"
        '<blockquote style="margin:0 0 0 .8ex;border-left:1px solid #ccc;padding-left:1ex">'
        f"{_text_to_html(_plain_source(message))}"
        "</blockquote></div>"
    )


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
        "body_html": _quote_html(message),
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
    source = _plain_source(message)
    when = _message_when(message)
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


def _forward_html(message: Message) -> str:
    """Gmail 风格的 HTML 转发块。"""
    rows = [
        ("发件人", message.from_addr or ""),
        ("日期", _message_when(message)),
        ("主题", message.subject or ""),
        ("收件人", message.to_addr or ""),
    ]
    if message.cc_addr:
        rows.append(("抄送", message.cc_addr))
    header = "".join(f"<div>{label}：{escape(value)}</div>" for label, value in rows)
    return (
        "<div><br></div><div><br></div>"
        '<div class="gmail_quote">'
        "<div>---------- 转发邮件 ----------</div>"
        f"{header}<br>"
        f"<div>{_text_to_html(_plain_source(message))}</div>"
        "</div>"
    )


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
        "body_html": _forward_html(message),
        "in_reply_to": "",
        "references": "",
        "is_forward": True,
        "forward_message_id": int(message.id),
        "forward_attachments": [(a.display_name or a.filename) for a in fwd_attachments],
    }


def _build_draft_prefill(db: Session, draft: Draft) -> dict:
    """打开草稿继续编辑：恢复各字段，以及回复 / 转发的上下文。"""
    prefill = {
        "draft_id": int(draft.id),
        "account_id": int(draft.account_id),
        "to": draft.to_addr or "",
        "cc": draft.cc_addr or "",
        "bcc": draft.bcc_addr or "",
        "subject": draft.subject or "",
        "body_text": draft.body_text or "",
        "body_html": sanitize_compose_html(draft.body_html) or _text_to_html(draft.body_text or ""),
        "in_reply_to": draft.in_reply_to or "",
        "references": draft.references or "",
        "is_reply": bool(draft.in_reply_to),
    }
    if draft.forward_message_id:
        original = db.execute(
            select(Message).options(selectinload(Message.attachments))
            .where(Message.id == draft.forward_message_id)
        ).scalar_one_or_none()
        if original is not None:
            prefill.update(
                is_forward=True,
                forward_message_id=int(original.id),
                forward_attachments=[
                    (a.display_name or a.filename) for a in original.attachments if not a.is_inline
                ],
            )
    return prefill


def _draft_is_empty(*values: Optional[str]) -> bool:
    return not any((v or "").strip() for v in values)


# ---- 写邮件 -------------------------------------------------------------------
# 收件人自动补全：扫描最近多少封邮件、最多返回多少个联系人。
_CONTACT_SCAN_LIMIT = 3000
_CONTACT_MAX = 300
_NOREPLY_HINTS = ("noreply", "no-reply", "donotreply", "do-not-reply", "mailer-daemon")


def _recent_contacts(db: Session) -> list[dict]:
    """从最近的往来邮件中收集联系人，按往来频率排序，用于收件人自动补全。

    发出邮件的收件人权重更高（更可能再次联系）；忽略自己的账号、垃圾邮件和 noreply 地址。
    """
    own = {email.lower() for email in db.execute(select(Account.email)).scalars().all()}
    scores: Counter[str] = Counter()
    names: dict[str, str] = {}

    def collect(header_values: list[Optional[str]], weight: int) -> None:
        for name, addr in getaddresses([v for v in header_values if v]):
            addr = (addr or "").strip().lower()
            if "@" not in addr or addr in own or any(h in addr for h in _NOREPLY_HINTS):
                continue
            scores[addr] += weight
            name = (name or "").strip().strip('"')
            if name and addr not in names:
                names[addr] = name

    rows = db.execute(
        select(Message.folder_kind, Message.from_addr, Message.to_addr, Message.cc_addr)
        .order_by(Message.id.desc())
        .limit(_CONTACT_SCAN_LIMIT)
    ).all()
    for kind, from_addr, to_addr, cc_addr in rows:
        if kind == "sent":
            collect([to_addr, cc_addr], 5)
        elif kind == "inbox":
            collect([from_addr], 1)
    for to_addr, cc_addr in db.execute(
        select(SentMessage.to_addr, SentMessage.cc_addr)
        .order_by(SentMessage.id.desc())
        .limit(_CONTACT_SCAN_LIMIT)
    ).all():
        collect([to_addr, cc_addr], 5)

    return [{"email": addr, "name": names.get(addr, "")} for addr, _ in scores.most_common(_CONTACT_MAX)]


def _enabled_accounts(db: Session):
    return db.execute(
        select(Account).where(Account.enabled.is_(True)).order_by(Account.email)
    ).scalars().all()


@router.get("/compose", name="compose.init")
def compose_init(
    request: Request,
    reply_to: Optional[int] = None,
    mode: Optional[str] = None,
    forward: Optional[int] = None,
    draft: Optional[int] = None,
    db: Session = Depends(get_db),
):
    """打开写邮件窗口：可用的发件账号、预填内容（回复 / 转发 / 草稿）和联系人。"""
    source_id = forward if forward is not None else reply_to
    if draft is not None:
        record = db.get(Draft, draft)
        if record is None:
            raise HTTPException(status_code=404, detail="Draft not found")
        prefill = _build_draft_prefill(db, record)
    elif source_id is not None:
        message = db.execute(
            select(Message)
            .options(selectinload(Message.account), selectinload(Message.attachments))
            .where(Message.id == source_id)
        ).scalar_one_or_none()
        if message is None:
            raise HTTPException(status_code=404, detail="Message not found")
        if forward is not None:
            prefill = _build_forward_prefill(message)
        else:
            prefill = _build_reply_prefill(message, reply_all=(mode == "all"))
    else:
        # 新邮件默认用当前选中的账号发送（与左侧导航一致）
        prefill = {"account_id": sidebar_data(request).preferred_account_id}

    return {
        "accounts": [
            {"id": int(a.id), "email": a.email, "display_name": a.display_name or ""}
            for a in _enabled_accounts(db)
        ],
        "prefill": prefill,
        "contacts": _recent_contacts(db),
    }


class DraftBody(BaseModel):
    draft_id: Optional[int] = None
    account_id: Optional[int] = None
    to: Optional[str] = None
    cc: Optional[str] = None
    bcc: Optional[str] = None
    subject: Optional[str] = None
    body_text: Optional[str] = None
    body_html: Optional[str] = None
    in_reply_to: Optional[str] = None
    references: Optional[str] = None
    forward_message_id: Optional[int] = None


@router.post("/drafts", name="compose.save_draft")
def save_draft(body: DraftBody, db: Session = Depends(get_db)):
    """写邮件时自动保存草稿（新建或更新）。内容全部清空时删除草稿。"""
    record = db.get(Draft, body.draft_id) if body.draft_id else None
    if _draft_is_empty(body.to, body.cc, body.bcc, body.subject, body.body_text):
        if record is not None:
            db.delete(record)
            db.commit()
        return {"id": None}
    account = db.get(Account, body.account_id or 0)
    if account is None:
        raise HTTPException(status_code=400, detail="Selected account is not available")
    if record is None:
        record = Draft(account_id=int(account.id))
        db.add(record)
    record.account_id = int(account.id)
    record.to_addr = body.to or ""
    record.cc_addr = body.cc or ""
    record.bcc_addr = body.bcc or ""
    record.subject = body.subject or ""
    record.body_text = body.body_text or ""
    record.body_html = body.body_html or ""
    record.in_reply_to = body.in_reply_to or None
    record.references = body.references or None
    record.forward_message_id = body.forward_message_id
    db.commit()
    return {"id": int(record.id), "saved_at": utc_to_local(record.updated_at).strftime("%H:%M")}


@router.post("/drafts/{draft_id}/delete", name="compose.delete_draft")
def delete_draft(draft_id: int, db: Session = Depends(get_db)):
    record = db.get(Draft, draft_id)
    if record is not None:
        db.delete(record)
        db.commit()
    return {"ok": True}


@router.get("/drafts", name="compose.drafts")
def drafts_list(db: Session = Depends(get_db)):
    drafts = db.execute(select(Draft).order_by(Draft.updated_at.desc())).scalars().all()
    return {
        "items": [
            {
                "id": int(d.id),
                "account_id": int(d.account_id),
                "to": d.to_addr or "",
                "to_short": ser.recipients(d.to_addr),
                "to_name": ser.addr_name(d.to_addr),
                "subject": d.subject or "",
                "snippet": ser.snippet(d.body_text),
                "updated_at": ser.iso_utc(d.updated_at),
            }
            for d in drafts
        ]
    }


def _unique_path(directory: Path, name: str) -> Path:
    """同名附件（如两个 image.png）依次改名为 image_1.png、image_2.png，不能互相覆盖。"""
    dest = directory / (name or "attachment")
    n = 1
    while dest.exists():
        dest = directory / f"{Path(name).stem}_{n}{Path(name).suffix}"
        n += 1
    return dest


@router.post("/compose/send", name="compose.send")
# 必须是普通 def：smtplib 发信和保存附件都是阻塞调用（最长 30 秒），FastAPI 会把
# 同步路由放进线程池；写成 async def 会卡住事件循环，期间所有请求和 SSE 都无响应。
def send(
    request: Request,
    account_id: int = Form(...),
    to: str = Form(""),
    cc: Optional[str] = Form(None),
    bcc: Optional[str] = Form(None),
    subject: str = Form(""),
    body_text: Optional[str] = Form(None),
    body_html: Optional[str] = Form(None),
    use_ssl: Optional[str] = Form("on"),
    in_reply_to: Optional[str] = Form(None),
    references: Optional[str] = Form(None),
    forward_message_id: Optional[str] = Form(None),
    draft_id: Optional[str] = Form(None),
    attachments: List[UploadFile] = File(default=[]),
    db: Session = Depends(get_db),
):
    """发送邮件。发送失败返回 400，前端保留已填写的内容（含附件）以便重试。"""
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
            dest = _unique_path(session_dir, Path(upload.filename).name)
            with dest.open("wb") as out:
                shutil.copyfileobj(upload.file, out)
            saved_paths.append(dest)

        # 转发时把原邮件的附件（不含内嵌图片）按原文件名复制进本次发送一起发出
        fwd_id = _coerce_int(forward_message_id)
        if fwd_id:
            originals = db.execute(
                select(Attachment).where(
                    Attachment.message_id == fwd_id,
                    Attachment.is_inline.is_(False),
                )
            ).scalars().all()
            if any(not (local_path(a) and local_path(a).is_file()) for a in originals):
                restore_message_files(fwd_id)   # 原邮件附件不在本机：先从 Gmail 补回
            for att in originals:
                src = local_path(att) or Path(att.file_path)
                if not src.is_file():
                    logger.warning("Forwarded attachment missing on disk: %s", att.file_path)
                    continue
                copy_name = Path(att.display_name or att.filename).name or src.name
                dest = _unique_path(session_dir, copy_name)
                try:
                    shutil.copyfile(src, dest)
                except OSError:
                    logger.warning("Failed to copy forwarded attachment: %s", att.file_path)
                    continue
                saved_paths.append(dest)

        try:
            result = send_email(
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
            message_id = result.message_id
            if result.refused:
                # 邮件已发给其余收件人；记为「部分退回」，在发件记录里提示哪些地址没收到
                status = "partial"
                error = "以下收件人被拒收：" + "；".join(
                    f"{addr}（{reason}）" for addr, reason in sorted(result.refused.items())
                )
            else:
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
        recipients = ", ".join(x for x in (to, cc, bcc) if x)[:300]
        if status != "failed":
            audit.record_request(request, "mail_sent", f"{account.email} → {recipients}：「{(subject or '(无主题)')[:80]}」")
        else:
            audit.record_request(request, "mail_send_failed", f"{account.email} → {recipients}：{(error or '')[:200]}")
        draft_record = db.get(Draft, _coerce_int(draft_id)) if _coerce_int(draft_id) else None
        if status != "failed" and draft_record is not None:
            db.delete(draft_record)   # 已发出，草稿不再需要
        db.commit()

        if status == "failed":
            raise HTTPException(status_code=400, detail=f"发送失败：{error}")
        return {"ok": True, "sent_id": int(record.id), "status": status, "error": error}
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


# ---- 发件记录 -----------------------------------------------------------------
def _normalize_message_id(value: Optional[str]) -> str:
    return (value or "").strip().strip("<>").strip().lower()


def _sent_item(source: str, row_id: int, account_id: int, to_addr: Optional[str], subject: Optional[str],
               body_text: Optional[str], time: Optional[datetime], status: str, error: Optional[str]) -> dict:
    return {
        "source": source,
        "id": row_id,
        "account_id": account_id,
        "to": to_addr or "",
        "to_short": ser.recipients(to_addr),
        "to_name": ser.addr_name(to_addr),
        "subject": subject or "",
        "snippet": ser.snippet(body_text),
        "time": ser.iso(time),
        "status": status,
        "error": error,
    }


@router.get("/sent", name="compose.sent_list")
def sent_list(db: Session = Depends(get_db)):
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

    # 部分收件人被拒收的发送：显示本地记录（带拒收提示），不显示 Gmail 里的同一封
    partial_ids = {
        _normalize_message_id(r.message_id) for r in local_rows if r.status == "partial" and r.message_id
    }
    items: list[tuple[Optional[datetime], dict]] = []
    for m in gmail_rows:
        if partial_ids and _normalize_message_id(m.message_id) in partial_ids:
            continue
        items.append((m.date, _sent_item(
            "gmail", int(m.id), int(m.account_id), m.to_addr, m.subject, m.body_text, m.date, "sent", None,
        )))
    for r in local_rows:
        if r.status == "sent" and _normalize_message_id(r.message_id) in synced_ids:
            continue
        local_time = utc_to_local(r.sent_at)
        items.append((local_time, _sent_item(
            "local", int(r.id), int(r.account_id), r.to_addr, r.subject, r.body_text, local_time, r.status, r.error,
        )))

    items.sort(key=lambda it: it[0] or datetime.min, reverse=True)
    return {
        "items": [item for _, item in items[:SENT_LIST_LIMIT]],
        "sync_sent_enabled": SYNC_SENT_ENABLED,
    }


@router.get("/sent/{sent_id}", name="compose.sent_detail")
def sent_detail(sent_id: int, db: Session = Depends(get_db)):
    record = db.execute(
        select(SentMessage)
        .options(selectinload(SentMessage.account))
        .where(SentMessage.id == sent_id)
    ).scalar_one_or_none()
    if record is None:
        raise HTTPException(status_code=404, detail="Sent message not found")
    account = record.account
    return {
        "id": int(record.id),
        "account_email": account.email if account else "",
        "account_name": (account.display_name or account.email) if account else "",
        "subject": record.subject or "",
        "to_full": ser.addr_list(record.to_addr),
        "cc_full": ser.addr_list(record.cc_addr) if record.cc_addr else "",
        "bcc_full": ser.addr_list(record.bcc_addr) if record.bcc_addr else "",
        "to_short": ser.recipients(record.to_addr),
        "cc_short": ser.recipients(record.cc_addr) if record.cc_addr else "",
        "time": ser.iso_utc(record.sent_at),
        "status": record.status,
        "error": record.error,
        "remote_images": body_has_remote_images(record.body_html) and not remote_images_allowed(),
    }


@router.post("/sent/{sent_id}/delete", name="compose.sent_delete")
def sent_delete(request: Request, sent_id: int, db: Session = Depends(get_db)):
    record = db.get(SentMessage, sent_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Sent message not found")
    audit.record_request(request, "sent_record_deleted", f"「{(record.subject or '(无主题)')[:80]}」→ {(record.to_addr or '')[:120]}")
    db.delete(record)
    db.commit()
    logger.info("Deleted sent record id=%s account_id=%s", sent_id, record.account_id)
    return {"ok": True}


@body_router.get("/sent/{sent_id}/body", response_class=HTMLResponse, name="compose.sent_body")
def sent_body(
    request: Request,
    sent_id: int,
    images: bool = Query(False),
    db: Session = Depends(get_db),
):
    """发件记录正文文档，只在详情页的 sandbox iframe 中加载。"""
    row = db.execute(
        select(SentMessage.body_html, SentMessage.body_text).where(SentMessage.id == sent_id)
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Sent message not found")
    return body_document_response(
        row.body_html, row.body_text,
        allow_remote_images=remote_images_allowed(images), origin=request_origin(request),
    )


