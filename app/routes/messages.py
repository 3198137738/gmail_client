"""Routes for browsing, viewing and updating read state of messages."""
from __future__ import annotations

import logging
from collections import defaultdict
from email.utils import parseaddr
import re
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session, selectinload

from ..auth import is_safe_redirect
from ..database import get_db
from ..models import Account, BlockedSender, Message
from ..services.background import submit_bg
from ..services import pagination
from ..services.pagination import Cursor
from ..services.search import message_search_filter
from ..services.mail_body import body_document_response, body_has_remote_images
from ..services.imap_client import is_account_busy
from ..services.imap_ops import delete_imap_messages, update_imap_flags
from ..services.message_actions import (
    delete_local_messages as _delete_local_messages,
    toggle_messages_read_state as _toggle_messages_read_state,
)
from ..templating import templates

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/messages", tags=["messages"])

DEFAULT_PAGE_SIZE = 25
PAGE_SIZE_OPTIONS = (25, 50, 100)
KIND_LABELS = {"inbox": "收件箱", "sent": "已发送", "junk": "垃圾邮件", "all": "全部邮件"}
SEEN_FLAG = b"\\Seen"

# Cookie name + lifetime for persisting the user's account filter choice.
# Selection is remembered for a year so it survives across normal browser
# sessions; user can clear it explicitly by selecting "全部账号".
PREFERRED_ACCOUNT_COOKIE = "gc_preferred_account"
COOKIE_MAX_AGE = 60 * 60 * 24 * 365


def _normalize_kind(kind: Optional[str]) -> str:
    if kind in KIND_LABELS:
        return kind
    return "inbox"


def _coerce_int(value) -> Optional[int]:
    """Tolerate empty strings coming from optional form/query fields."""
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalize_page_size(value) -> int:
    page_size = _coerce_int(value)
    if page_size in PAGE_SIZE_OPTIONS:
        return int(page_size)
    return DEFAULT_PAGE_SIZE


def _normalize_sender_email(value: Optional[str]) -> str:
    """Extract bare email address from a header value and lowercase it."""
    raw = (value or "").strip()
    _name, addr = parseaddr(raw)
    candidate = (addr or "").strip().lower()
    if candidate:
        return candidate
    # Fallback for malformed From headers that parseaddr can't decode.
    m = re.search(r"[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}", raw, re.IGNORECASE)
    return (m.group(0).strip().lower() if m else "")


def _kind_condition(kind: str):
    """按文件夹类型过滤；与 Gmail 一致，「全部邮件」不包含垃圾邮件。"""
    if kind == "all":
        return Message.folder_kind != "junk"
    return Message.folder_kind == kind


def _apply_kind_account_filters(stmt, kind: str, account_id: Optional[int]):
    stmt = stmt.where(_kind_condition(kind))
    if account_id:
        stmt = stmt.where(Message.account_id == account_id)
    return stmt


def _resolve_selected_account(
    raw_query_value: Optional[str],
    cookie_value: Optional[str],
    valid_ids: set[int],
) -> tuple[Optional[int], Optional[str]]:
    """Decide which account is currently active.

    Precedence:
      1. Explicit query string (even an empty string counts as a deliberate
         choice of "all"). This updates the cookie.
      2. Cookie from a previous explicit choice.
      3. Default: all accounts.

    Returns ``(account_id, cookie_to_persist)``. ``cookie_to_persist`` is
    ``None`` when no cookie update is required.
    """
    if raw_query_value is not None:
        cleaned = raw_query_value.strip().lower()
        if cleaned in ("", "all", "0"):
            return None, "all"
        try:
            aid = int(raw_query_value)
        except (TypeError, ValueError):
            return None, "all"
        if aid not in valid_ids:
            # Selected account no longer exists - fall back to all and clear pref.
            return None, "all"
        return aid, str(aid)

    if cookie_value:
        cleaned = cookie_value.strip().lower()
        if cleaned in ("all", "0"):
            return None, None
        try:
            aid = int(cookie_value)
        except (TypeError, ValueError):
            return None, None
        if aid in valid_ids:
            return aid, None
        # Cookie references a stale account - reset to all silently.
        return None, "all"

    return None, None


def _apply_cookie(response: Response, value: Optional[str]) -> None:
    if value is None:
        return
    response.set_cookie(
        PREFERRED_ACCOUNT_COOKIE,
        value,
        max_age=COOKIE_MAX_AGE,
        samesite="lax",
        httponly=False,
        path="/",
    )


def _redirect_to_list(
    request: Request,
    *,
    kind: str,
    account_id: Optional[int],
    q: Optional[str],
    page: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
    unread: bool = False,
) -> RedirectResponse:
    params: dict[str, object] = {
        "kind": kind,
        "q": q or "",
        "page": max(page, 1),
        "page_size": _normalize_page_size(page_size),
    }
    if unread:
        params["unread"] = 1
    if account_id:
        params["account_id"] = account_id
    return RedirectResponse(
        url=request.url_for("messages.list").include_query_params(**params),
        status_code=303,
    )


def _load_messages_by_ids(db: Session, raw_ids: list[int]) -> list[Message]:
    message_ids = sorted({int(mid) for mid in raw_ids if mid})
    if not message_ids:
        return []
    return db.execute(
        select(Message)
        .options(selectinload(Message.attachments))
        .where(Message.id.in_(message_ids))
    ).scalars().all()


def _group_messages_by_account_and_folder(
    messages: list[Message],
) -> dict[int, dict[str, list[Message]]]:
    grouped: dict[int, dict[str, list[Message]]] = defaultdict(lambda: defaultdict(list))
    for message in messages:
        grouped[int(message.account_id)][message.folder].append(message)
    return grouped


def _schedule_remote_delete(messages: list[Message]) -> None:
    """Delete on IMAP in background so UI doesn't block on network."""
    grouped = _group_messages_by_account_and_folder(messages)
    for acc_id, folder_map in grouped.items():
        pairs = [
            (folder, [int(msg.uid) for msg in folder_messages])
            for folder, folder_messages in folder_map.items()
        ]
        submit_bg(delete_imap_messages, acc_id, pairs)


@router.get("/", response_class=HTMLResponse, name="messages.list")
def list_messages(
    request: Request,
    kind: Optional[str] = Query("inbox"),
    account_id: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(DEFAULT_PAGE_SIZE),
    after: Optional[str] = Query(None),
    before: Optional[str] = Query(None),
    unread: bool = Query(False),
    db: Session = Depends(get_db),
):
    kind = _normalize_kind(kind)
    page_size = _normalize_page_size(page_size)
    accounts = db.execute(select(Account).order_by(Account.email)).scalars().all()
    valid_ids = {int(a.id) for a in accounts}

    cookie_value = request.cookies.get(PREFERRED_ACCOUNT_COOKIE)
    selected_account_id, cookie_to_persist = _resolve_selected_account(
        account_id, cookie_value, valid_ids
    )

    q_clean = (q or "").strip()
    search_filter = message_search_filter(q_clean)

    # Main result query (kind + account + search).
    stmt = select(Message).options(selectinload(Message.account))
    # 总数与未读数合并为一次查询
    count_stmt = select(
        func.count(Message.id),
        func.coalesce(func.sum(case((Message.is_read.is_(False), 1), else_=0)), 0),
    )
    stmt = _apply_kind_account_filters(stmt, kind, selected_account_id)
    count_stmt = _apply_kind_account_filters(count_stmt, kind, selected_account_id)
    if search_filter is not None:
        stmt = stmt.where(search_filter)
        count_stmt = count_stmt.where(search_filter)
    if unread:
        # 仅看未读
        stmt = stmt.where(Message.is_read.is_(False))
        count_stmt = count_stmt.where(Message.is_read.is_(False))

    total, unread_total = db.execute(count_stmt).one()
    total = int(total or 0)
    unread_total = int(unread_total or 0)
    pages = max(1, (total + page_size - 1) // page_size)
    page = min(page, pages)

    after_cursor = Cursor.decode(after)
    before_cursor = Cursor.decode(before)
    if page > 1 and after_cursor is not None:
        # 下一页：从上一页最后一封之后取
        stmt = stmt.where(pagination.after(after_cursor)).order_by(*pagination.forward_order())
        messages = db.execute(stmt.limit(page_size)).scalars().all()
    elif page > 1 and before_cursor is not None:
        # 上一页：逆序取游标之前最近的若干封，再翻转回显示顺序
        stmt = stmt.where(pagination.before(before_cursor)).order_by(*pagination.backward_order())
        messages = list(reversed(db.execute(stmt.limit(page_size)).scalars().all()))
    elif page > 1 and page == pages:
        # 末页：逆序取最后几封，同样不需要 OFFSET
        tail = total - (pages - 1) * page_size
        stmt = stmt.order_by(*pagination.backward_order())
        messages = list(reversed(db.execute(stmt.limit(tail)).scalars().all()))
    else:
        # 第一页，或只带了页码的旧链接/操作后跳转：按 OFFSET 兜底
        stmt = stmt.order_by(*pagination.forward_order())
        stmt = stmt.offset((page - 1) * page_size).limit(page_size)
        messages = db.execute(stmt).scalars().all()

    next_cursor = Cursor.of(messages[-1]).encode() if messages else None
    prev_cursor = Cursor.of(messages[0]).encode() if messages else None

    # Convenience indices for the Roundcube-style "first - last / total" footer.
    first_index = (page - 1) * page_size + 1 if total else 0
    last_index = first_index + len(messages) - 1 if first_index else 0

    # 侧边栏未读数：按「账号 + 文件夹类型」统计，与当前视图、搜索、所选账号无关，
    # 保证每个文件夹旁边显示的始终是它自己的未读数。
    unread_rows = db.execute(
        select(Message.account_id, Message.folder_kind, func.count(Message.id))
        .where(Message.is_read.is_(False))
        .group_by(Message.account_id, Message.folder_kind)
    ).all()
    unread_by_account: dict[int, int] = {}
    junk_unread_by_account: dict[int, int] = {}
    for acc_id, folder_kind, count in unread_rows:
        if folder_kind == "inbox":
            unread_by_account[acc_id] = count
        elif folder_kind == "junk":
            junk_unread_by_account[acc_id] = count
    nav_unread_inbox = sum(unread_by_account.values())
    nav_unread_junk = sum(junk_unread_by_account.values())

    busy_by_account = {int(a.id): is_account_busy(int(a.id)) for a in accounts}
    any_busy = any(busy_by_account.values())

    selected_account = next(
        (a for a in accounts if int(a.id) == selected_account_id),
        None,
    )

    response = templates.TemplateResponse(
        request,
        "messages.html",
        {
            "messages": messages,
            "accounts": accounts,
            "current_account_id": selected_account_id,
            "selected_account": selected_account,
            "kind": kind,
            "kind_label": KIND_LABELS.get(kind, kind),
            "q": q_clean,
            "page": page,
            "pages": pages,
            "total": total,
            "unread_total": unread_total,
            "unread_by_account": unread_by_account,
            "junk_unread_by_account": junk_unread_by_account,
            "busy_by_account": busy_by_account,
            "any_busy": any_busy,
            "nav_unread_inbox": nav_unread_inbox,
            "nav_unread_junk": nav_unread_junk,
            "first_index": first_index,
            "last_index": last_index,
            "page_size": page_size,
            "page_size_options": PAGE_SIZE_OPTIONS,
            "next_cursor": next_cursor,
            "unread_only": unread,
            "prev_cursor": prev_cursor,
        },
    )
    _apply_cookie(response, cookie_to_persist)
    return response


@router.post("/mark-all-read", name="messages.mark_all_read")
def mark_all_read(
    request: Request,
    kind: str = Form("inbox"),
    account_id: Optional[str] = Form(None),
    q: Optional[str] = Form(None),
    page: int = Form(1),
    page_size: int = Form(DEFAULT_PAGE_SIZE),
    unread: bool = Form(False),
    db: Session = Depends(get_db),
):
    """Mark every unread message matching the current filter as read.

    Local DB is updated immediately; the IMAP server's ``\\Seen`` flag is then
    updated on a best-effort basis (failures are logged but don't roll back
    the local change).
    """
    kind = _normalize_kind(kind)
    account_id = _coerce_int(account_id)

    stmt = select(Message).where(Message.is_read.is_(False))
    stmt = _apply_kind_account_filters(stmt, kind, account_id)
    search_filter = message_search_filter(q)
    if search_filter is not None:
        stmt = stmt.where(search_filter)

    unread = db.execute(stmt).scalars().all()
    affected = _toggle_messages_read_state(db, unread, is_read=True)

    logger.info(
        "mark-all-read kind=%s account_id=%s affected=%s",
        kind, account_id, affected,
    )
    return _redirect_to_list(
        request,
        kind=kind,
        account_id=account_id,
        q=q,
        page=page,
        page_size=page_size,
        unread=unread,
    )


@router.post("/batch-mark-read", name="messages.batch_mark_read")
def batch_mark_read(
    request: Request,
    message_ids: list[int] = Form(default=[]),
    kind: str = Form("inbox"),
    account_id: Optional[str] = Form(None),
    q: Optional[str] = Form(None),
    page: int = Form(1),
    page_size: int = Form(DEFAULT_PAGE_SIZE),
    unread: bool = Form(False),
    db: Session = Depends(get_db),
):
    account_id_int = _coerce_int(account_id)
    messages = _load_messages_by_ids(db, message_ids)
    affected = _toggle_messages_read_state(db, messages, is_read=True)
    logger.info(
        "batch-mark-read kind=%s account_id=%s selected=%s affected=%s",
        kind, account_id_int, len(message_ids), affected,
    )
    return _redirect_to_list(
        request,
        kind=_normalize_kind(kind),
        account_id=account_id_int,
        q=q,
        page=page,
        page_size=page_size,
        unread=unread,
    )


@router.post("/batch-mark-unread", name="messages.batch_mark_unread")
def batch_mark_unread(
    request: Request,
    message_ids: list[int] = Form(default=[]),
    kind: str = Form("inbox"),
    account_id: Optional[str] = Form(None),
    q: Optional[str] = Form(None),
    page: int = Form(1),
    page_size: int = Form(DEFAULT_PAGE_SIZE),
    unread: bool = Form(False),
    db: Session = Depends(get_db),
):
    account_id_int = _coerce_int(account_id)
    messages = _load_messages_by_ids(db, message_ids)
    affected = _toggle_messages_read_state(db, messages, is_read=False)
    logger.info(
        "batch-mark-unread kind=%s account_id=%s selected=%s affected=%s",
        kind, account_id_int, len(message_ids), affected,
    )
    return _redirect_to_list(
        request,
        kind=_normalize_kind(kind),
        account_id=account_id_int,
        q=q,
        page=page,
        page_size=page_size,
        unread=unread,
    )


@router.post("/batch-delete", name="messages.batch_delete")
def batch_delete(
    request: Request,
    message_ids: list[int] = Form(default=[]),
    kind: str = Form("inbox"),
    account_id: Optional[str] = Form(None),
    q: Optional[str] = Form(None),
    page: int = Form(1),
    page_size: int = Form(DEFAULT_PAGE_SIZE),
    unread: bool = Form(False),
    db: Session = Depends(get_db),
):
    account_id_int = _coerce_int(account_id)
    messages = _load_messages_by_ids(db, message_ids)
    _schedule_remote_delete(messages)
    deleted = _delete_local_messages(db, messages)
    logger.info(
        "batch-delete kind=%s account_id=%s selected=%s deleted=%s mode=async_remote",
        kind, account_id_int, len(message_ids), deleted,
    )
    return _redirect_to_list(
        request,
        kind=_normalize_kind(kind),
        account_id=account_id_int,
        q=q,
        page=page,
        page_size=page_size,
        unread=unread,
    )


@router.post("/{message_id}/mark-unread", name="messages.mark_unread")
def mark_unread(
    request: Request,
    message_id: int,
    next: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    message = db.get(Message, message_id)
    if message is None:
        raise HTTPException(status_code=404, detail="Message not found")
    _toggle_messages_read_state(db, [message], is_read=False)
    if is_safe_redirect(next):
        return RedirectResponse(url=next, status_code=303)
    return RedirectResponse(url=request.url_for("messages.detail", message_id=message_id), status_code=303)


@router.post("/{message_id}/delete", name="messages.delete_one")
def delete_one(
    request: Request,
    message_id: int,
    next: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    message = db.execute(
        select(Message)
        .options(selectinload(Message.attachments))
        .where(Message.id == message_id)
    ).scalar_one_or_none()
    if message is None:
        raise HTTPException(status_code=404, detail="Message not found")

    _schedule_remote_delete([message])
    _delete_local_messages(db, [message])
    logger.info("delete-one message_id=%s account_id=%s mode=async_remote", message_id, message.account_id)

    if is_safe_redirect(next):
        return RedirectResponse(url=next, status_code=303)
    return _redirect_to_list(
        request,
        kind=message.folder_kind,
        account_id=int(message.account_id),
        q="",
        page=1,
        page_size=DEFAULT_PAGE_SIZE,
    )


def _list_back_url(request: Request, back: Optional[str], folder_kind: str) -> str:
    """详情页「返回」的目标：打开邮件前所在的列表（保留账号、页码、筛选、搜索）。

    只接受站内的邮件列表地址；没有或不合法时回到该文件夹列表首页。
    """
    if back and is_safe_redirect(back) and back.startswith("/messages/?"):
        return back
    return str(request.url_for("messages.list").include_query_params(kind=folder_kind))


@router.get("/{message_id}/body", response_class=HTMLResponse, name="messages.body")
def message_body(
    message_id: int,
    images: bool = Query(False),
    db: Session = Depends(get_db),
):
    """邮件正文文档，只在详情页的 sandbox iframe 中加载。"""
    row = db.execute(
        select(Message.body_html, Message.body_text).where(Message.id == message_id)
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Message not found")
    return body_document_response(row.body_html, row.body_text, allow_remote_images=images)


@router.get("/{message_id}", response_class=HTMLResponse, name="messages.detail")
def view_message(
    request: Request,
    message_id: int,
    images: bool = Query(False),
    back: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    message = db.execute(
        select(Message)
        .options(selectinload(Message.attachments), selectinload(Message.account))
        .where(Message.id == message_id)
    ).scalar_one_or_none()
    if message is None:
        raise HTTPException(status_code=404, detail="Message not found")

    needs_imap_seen = not message.is_read
    if needs_imap_seen:
        account_id = int(message.account_id)
        folder = message.folder
        uid = int(message.uid)
        message.is_read = True
        db.commit()
        submit_bg(update_imap_flags, account_id, [(folder, [uid])], add_flags=[SEEN_FLAG])

    file_attachments = [a for a in message.attachments if not a.is_inline]
    inline_attachments = [a for a in message.attachments if a.is_inline]
    sender_email = _normalize_sender_email(message.from_addr)
    sender_blocked = False
    if sender_email:
        sender_blocked = (
            db.execute(
                select(BlockedSender.id).where(
                    BlockedSender.account_id == message.account_id,
                    BlockedSender.sender_email == sender_email,
                )
            ).first()
            is not None
        )

    return templates.TemplateResponse(
        request,
        "message_detail.html",
        {
            "message": message,
            "file_attachments": file_attachments,
            "inline_attachments": inline_attachments,
            "sender_email": sender_email,
            "sender_blocked": sender_blocked,
            "show_images": images,
            "back_url": _list_back_url(request, back, message.folder_kind),
            "has_remote_images": body_has_remote_images(message.body_html),
        },
    )
