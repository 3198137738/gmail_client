"""Routes for browsing, viewing and updating read state of messages."""
from __future__ import annotations

import logging
from collections import defaultdict
from email.utils import parseaddr
import re
from typing import Optional
from urllib.parse import parse_qs, urlparse

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session, defer, selectinload

from ..auth import is_safe_redirect
from ..database import get_db
from ..models import Account, Attachment, BlockedSender, Label, Message, MessageLabel
from ..services import pagination
from ..services.pagination import Cursor
from ..services.search import message_search_filter
from ..services.mail_body import (
    body_document_response,
    body_has_remote_images,
    remote_images_allowed,
)
from ..services.imap_client import is_account_busy
from ..services import labels as gmail_labels
from ..services import audit, outbox, threads
from ..services.message_actions import (
    delete_local_messages as _delete_local_messages,
    set_starred as _set_starred,
    toggle_messages_read_state as _toggle_messages_read_state,
)
from ..templating import templates

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/messages", tags=["messages"])

DEFAULT_PAGE_SIZE = 25
PAGE_SIZE_OPTIONS = (25, 50, 100)
KIND_LABELS = {"inbox": "收件箱", "sent": "已发送", "junk": "垃圾邮件", "all": "全部邮件", "starred": "已加星标"}
SEEN_FLAG = b"\\Seen"

# Cookie name + lifetime for persisting the user's account filter choice.
# Selection is remembered for a year so it survives across normal browser
# sessions; user can clear it explicitly by selecting "全部账号".
PREFERRED_ACCOUNT_COOKIE = "gc_preferred_account"
COOKIE_MAX_AGE = 60 * 60 * 24 * 365


_LABEL_KIND_RE = re.compile(r"label-(\d+)")


def _normalize_kind(kind: Optional[str]) -> str:
    """文件夹类型；Gmail 标签视图写作 label-<标签 id>。"""
    if kind in KIND_LABELS:
        return kind
    if kind and _LABEL_KIND_RE.fullmatch(kind):
        return kind
    return "inbox"


def _label_id_of(kind: str) -> Optional[int]:
    match = _LABEL_KIND_RE.fullmatch(kind or "")
    return int(match.group(1)) if match else None


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
    if kind == "starred":
        # 与 Gmail 一致：垃圾邮件中的星标邮件不显示在「已加星标」里
        return (Message.is_starred.is_(True)) & (Message.folder_kind != "junk")
    label_id = _label_id_of(kind)
    if label_id is not None:
        return Message.id.in_(
            select(MessageLabel.message_id).where(MessageLabel.label_id == label_id)
        ) & (Message.folder_kind != "junk")
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


def _load_for_action(request: Request, db: Session, raw_ids: list[int]) -> list[Message]:
    """批量操作的目标邮件：会话视图下选中一行代表整个会话（与 Gmail 一致）。"""
    messages = _load_messages_by_ids(db, raw_ids)
    return threads.expand(db, messages) if threads.conversation_mode(request) else messages


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


def _enqueue_remote_delete(db: Session, messages: list[Message]) -> list[int]:
    """把远程删除写入 outbox（与本地删除同一事务提交），返回涉及的账号 id。"""
    grouped = _group_messages_by_account_and_folder(messages)
    for acc_id, folder_map in grouped.items():
        for folder, folder_messages in folder_map.items():
            outbox.enqueue(db, acc_id, folder, outbox.OP_DELETE, [int(m.uid) for m in folder_messages])
    return list(grouped)


SNIPPET_SOURCE_CHARS = 300


def _load_snippets(db: Session, ids: list[int]) -> dict[int, str]:
    """列表中「主题 - 摘要」的摘要：只取正文开头一段，合并空白。"""
    if not ids:
        return {}
    rows = db.execute(
        select(Message.id, func.substr(Message.body_text, 1, SNIPPET_SOURCE_CHARS))
        .where(Message.id.in_(ids))
    ).all()
    return {int(mid): " ".join((text or "").split())[:200] for mid, text in rows}


def _load_attachment_names(db: Session, ids: list[int]) -> dict[int, list[Attachment]]:
    """列表行下方显示的附件卡片（不含内嵌图片）。"""
    if not ids:
        return {}
    rows = db.execute(
        select(Attachment)
        .where(Attachment.message_id.in_(ids), Attachment.is_inline.is_(False))
        .order_by(Attachment.id)
    ).scalars().all()
    result: dict[int, list[Attachment]] = {}
    for att in rows:
        result.setdefault(int(att.message_id), []).append(att)
    return result


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
    # 列表不需要完整正文（body_html 可能有几 MB），摘要和附件名在下面单独查询。
    stmt = select(Message).options(
        selectinload(Message.account),
        defer(Message.body_html),
        defer(Message.body_text),
    )
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
    conversation = threads.conversation_mode(request)
    if conversation:
        # 会话视图：每个会话只取符合条件的最新一封；总数按会话计（未读数仍按邮件计）
        view_conditions = [_kind_condition(kind)]
        if selected_account_id:
            view_conditions.append(Message.account_id == selected_account_id)
        if search_filter is not None:
            view_conditions.append(search_filter)
        if unread:
            view_conditions.append(Message.is_read.is_(False))
        rep_condition = threads.representative_condition(view_conditions)
        stmt = stmt.where(rep_condition)
        total = int(db.execute(select(func.count(Message.id)).where(*view_conditions, rep_condition)).scalar_one())
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

    page_ids = [int(m.id) for m in messages]
    thread_stats = threads.thread_stats(db, messages) if conversation else {}
    message_labels = gmail_labels.labels_by_message(db, page_ids)
    kind_label = KIND_LABELS.get(kind, kind)
    current_label = db.get(Label, _label_id_of(kind)) if _label_id_of(kind) else None
    if current_label is not None:
        kind_label = current_label.name
    snippets = _load_snippets(db, page_ids)
    attachments_by_message = _load_attachment_names(db, page_ids)

    next_cursor = Cursor.of(messages[-1]).encode() if messages else None
    prev_cursor = Cursor.of(messages[0]).encode() if messages else None

    # Convenience indices for the Roundcube-style "first - last / total" footer.
    first_index = (page - 1) * page_size + 1 if total else 0
    last_index = first_index + len(messages) - 1 if first_index else 0

    # 侧栏未读数由 navigation.sidebar_data 统一查询（同一请求内只查一次），这里不再重复统计
    any_busy = any(is_account_busy(int(a.id)) for a in accounts)

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
            "kind_label": kind_label,
            "current_label": current_label,
            "message_labels": message_labels,
            "q": q_clean,
            "page": page,
            "pages": pages,
            "total": total,
            "unread_total": unread_total,
            "any_busy": any_busy,
            "conversation": conversation,
            "thread_stats": thread_stats,
            "first_index": first_index,
            "last_index": last_index,
            "page_size": page_size,
            "page_size_options": PAGE_SIZE_OPTIONS,
            "next_cursor": next_cursor,
            "snippets": snippets,
            "attachments_by_message": attachments_by_message,
            "unread_only": unread,
            "prev_cursor": prev_cursor,
        },
    )
    _apply_cookie(response, cookie_to_persist)
    return response


def _thread_labels(db: Session, thread: list[Message]) -> list[dict]:
    """会话中所有邮件的标签（去重，按名称排序）。"""
    merged: dict[int, dict] = {}
    for items in gmail_labels.labels_by_message(db, [int(m.id) for m in thread]).values():
        for item in items:
            merged[item["id"]] = item
    return sorted(merged.values(), key=lambda item: item["name"])


@router.post("/{message_id}/labels", name="messages.set_labels")
def set_labels(
    request: Request,
    message_id: int,
    label_ids: list[int] = Form(default=[]),
    next: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    """设置邮件（会话视图下为整个会话）的 Gmail 标签：勾选的加上，未勾选的去掉。"""
    message = db.get(Message, message_id)
    if message is None:
        raise HTTPException(status_code=404, detail="Message not found")
    targets = threads.expand(db, [message]) if threads.conversation_mode(request) else [message]
    account_labels = {
        int(label.id): label
        for label in db.execute(select(Label).where(Label.account_id == message.account_id)).scalars()
    }
    wanted = {i for i in label_ids if i in account_labels}
    current = gmail_labels.labels_by_message(db, [int(m.id) for m in targets])
    # (文件夹, 标签名, 操作) -> UID 列表
    ops: dict[tuple[str, str, str], list[int]] = defaultdict(list)
    for m in targets:
        have = {item["id"] for item in current.get(int(m.id), [])}
        for label_id in wanted - have:
            db.add(MessageLabel(message_id=int(m.id), label_id=label_id))
            ops[(m.folder, account_labels[label_id].name, outbox.OP_ADD_LABEL)].append(int(m.uid))
        for label_id in (have - wanted) & set(account_labels):
            db.execute(MessageLabel.__table__.delete().where(
                MessageLabel.message_id == int(m.id), MessageLabel.label_id == label_id
            ))
            ops[(m.folder, account_labels[label_id].name, outbox.OP_REMOVE_LABEL)].append(int(m.uid))
    for (folder, name, op), uids in ops.items():
        outbox.enqueue(db, int(message.account_id), folder, op, uids, name)
    db.commit()
    if ops:
        outbox.kick([int(message.account_id)])
    if is_safe_redirect(next):
        return RedirectResponse(url=next, status_code=303)
    return RedirectResponse(url=request.url_for("messages.detail", message_id=message_id), status_code=303)


@router.post("/conversation-view", name="messages.conversation_view")
def set_conversation_view(
    request: Request,
    on: str = Form("1"),
    next: Optional[str] = Form(None),
):
    """开启 / 关闭会话视图（记在 cookie 中）。"""
    target = next if is_safe_redirect(next) else str(request.url_for("messages.list"))
    response = RedirectResponse(url=target, status_code=303)
    response.set_cookie(
        threads.CONVERSATION_COOKIE,
        "1" if on == "1" else "0",
        max_age=COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
    )
    return response


@router.get("/nav", response_class=HTMLResponse, name="messages.nav")
def nav_partial(
    request: Request,
    kind: Optional[str] = Query("inbox"),
    account_id: Optional[str] = Query(None),
    unread: bool = Query(False),
    db: Session = Depends(get_db),
):
    """只渲染左侧导航（各文件夹未读数），供列表页实时刷新侧栏用，不查询邮件列表。

    参数与列表页相同（前端直接带上当前地址的查询串），以便高亮当前文件夹和账号。
    """
    valid_ids = {int(a) for a in db.execute(select(Account.id)).scalars()}
    selected_account_id, _ = _resolve_selected_account(
        account_id, request.cookies.get(PREFERRED_ACCOUNT_COOKIE), valid_ids
    )
    return templates.TemplateResponse(
        request,
        "_sidebar.html",
        {"kind": _normalize_kind(kind), "current_account_id": selected_account_id, "unread_only": unread},
    )


@router.post("/{message_id}/star", name="messages.star")
def toggle_star(
    request: Request,
    message_id: int,
    starred: Optional[str] = Form(None),
    next: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    """加 / 取消星标。``starred`` 为空时切换。页面脚本（fetch）调用时返回 JSON。"""
    message = db.get(Message, message_id)
    if message is None:
        raise HTTPException(status_code=404, detail="Message not found")
    value = (starred in ("1", "true", "on")) if starred is not None else not message.is_starred
    _set_starred(db, [message], value)
    if request.headers.get("x-requested-with") == "fetch":
        return JSONResponse({"id": message_id, "starred": value})
    if is_safe_redirect(next):
        return RedirectResponse(url=next, status_code=303)
    return RedirectResponse(url=request.url_for("messages.detail", message_id=message_id), status_code=303)


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
    messages = _load_for_action(request, db, message_ids)
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
    messages = _load_for_action(request, db, message_ids)
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
    messages = _load_for_action(request, db, message_ids)
    if messages:
        audit.record_request(request, "mail_deleted", audit.summarize_subjects([m.subject for m in messages]))
    touched = _enqueue_remote_delete(db, messages)
    deleted = _delete_local_messages(db, messages)
    outbox.kick(touched)
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
    targets = threads.expand(db, [message]) if threads.conversation_mode(request) else [message]
    _toggle_messages_read_state(db, targets, is_read=False)
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

    targets = threads.expand(db, [message]) if threads.conversation_mode(request) else [message]
    audit.record_request(request, "mail_deleted", audit.summarize_subjects([m.subject for m in targets]))
    touched = _enqueue_remote_delete(db, targets)
    _delete_local_messages(db, targets)
    outbox.kick(touched)
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
    return f"/messages/?kind={_normalize_kind(folder_kind)}"


def _list_conditions(back_url: str, fallback_kind: str) -> list:
    """从列表地址中取出筛选条件（文件夹、账号、搜索、仅未读），与列表页一致。"""
    qs = parse_qs(urlparse(back_url).query)

    def first(name: str) -> Optional[str]:
        values = qs.get(name)
        return values[0] if values else None

    kind = _normalize_kind(first("kind") or fallback_kind)
    conditions = [_kind_condition(kind)]
    account_id = _coerce_int(first("account_id"))
    if account_id:
        conditions.append(Message.account_id == account_id)
    search = message_search_filter(first("q"))
    if search is not None:
        conditions.append(search)
    if first("unread") in ("1", "true"):
        conditions.append(Message.is_read.is_(False))
    return conditions


def _position_in_list(db: Session, message: Message, back_url: str, conversation: bool = False) -> dict:
    """详情页右上角「第 N 封，共 M 封」以及较新 / 较旧邮件（与列表的排序一致）。"""
    conditions = _list_conditions(back_url, message.folder_kind)
    if conversation:
        conditions.append(threads.representative_condition(list(conditions)))
    cursor = Cursor.of(message)
    count = lambda *extra: db.execute(  # noqa: E731
        select(func.count(Message.id)).where(*conditions, *extra)
    ).scalar_one()

    newer_id = db.execute(
        select(Message.id).where(*conditions, pagination.before(cursor))
        .order_by(*pagination.backward_order()).limit(1)
    ).scalar()
    older_id = db.execute(
        select(Message.id).where(*conditions, pagination.after(cursor))
        .order_by(*pagination.forward_order()).limit(1)
    ).scalar()
    total = count()
    # 当前邮件可能已不满足条件（如「仅看未读」时刚被标为已读），仍算在内
    if not db.execute(select(Message.id).where(Message.id == message.id, *conditions)).first():
        total += 1
    return {
        "position": count(pagination.before(cursor)) + 1,
        "total": total,
        "newer_id": newer_id,
        "older_id": older_id,
    }


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
    return body_document_response(row.body_html, row.body_text, allow_remote_images=remote_images_allowed(images))


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

    conversation = threads.conversation_mode(request)
    thread = threads.thread_messages(db, message) if conversation else [message]
    unread_in_thread = [m for m in thread if not m.is_read]
    # 默认展开：未读的邮件、当前打开的邮件，以及会话中最后一封（与 Gmail 一致）
    expanded_ids = {int(m.id) for m in unread_in_thread} | {int(message.id), int(thread[-1].id)}
    if unread_in_thread:
        _toggle_messages_read_state(db, unread_in_thread, is_read=True)

    file_attachments = [a for a in message.attachments if not a.is_inline]
    inline_attachments = [a for a in message.attachments if a.is_inline]
    back_url = _list_back_url(request, back, message.folder_kind)
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
            "thread": thread,
            "expanded_ids": expanded_ids,
            "thread_labels": _thread_labels(db, thread),
            "account_labels": db.execute(
                select(Label).where(Label.account_id == message.account_id).order_by(Label.name)
            ).scalars().all(),
            "remote_images": {int(m.id): body_has_remote_images(m.body_html) for m in thread},
            "file_attachments": file_attachments,
            "inline_attachments": inline_attachments,
            "sender_email": sender_email,
            "sender_blocked": sender_blocked,
            "show_images": remote_images_allowed(images),
            "back_url": back_url,
            "list_position": _position_in_list(db, message, back_url, conversation),
            "has_remote_images": body_has_remote_images(message.body_html),
        },
    )
