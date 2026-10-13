"""邮件列表、详情和已读 / 星标 / 删除 / 标签等操作的接口。"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timezone
from email.utils import parseaddr
import re
from typing import Optional
from urllib.parse import parse_qs, urlparse

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session, defer, selectinload

from .. import serializers as ser
from ..auth import is_safe_redirect
from ..database import get_db
from ..models import Account, Attachment, BlockedSender, Label, Message, MessageLabel
from ..navigation import sidebar_data
from ..services import pagination
from ..services.pagination import Cursor
from ..services.search import message_search_filter
from ..services.mail_body import (
    body_document_response,
    body_has_remote_images,
    remote_images_allowed,
    request_origin,
)
from ..services.imap_client import is_account_busy
from ..timeutil import utcnow
from ..services import labels as gmail_labels
from ..services import audit, outbox, threads
from ..services import remote_search as remote_search_service
from ..services.message_actions import (
    delete_local_messages as _delete_local_messages,
    set_starred as _set_starred,
    toggle_messages_read_state as _toggle_messages_read_state,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/messages", tags=["messages"])
# 不是 JSON 接口的部分：邮件正文文档（在详情页的 sandbox iframe 中加载）
body_router = APIRouter(prefix="/messages", tags=["messages"])

DEFAULT_PAGE_SIZE = 25
PAGE_SIZE_OPTIONS = (25, 50, 100)
KIND_LABELS = {
    "inbox": "收件箱", "sent": "已发送", "junk": "垃圾邮件", "all": "全部邮件", "starred": "已加星标", "snoozed": "已延后",
}

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
    """按文件夹类型过滤；与 Gmail 一致，「全部邮件」不包含垃圾邮件。延后的邮件只在「已延后」中显示。"""
    if kind == "snoozed":
        return Message.snoozed_until.is_not(None)
    not_snoozed = Message.snoozed_until.is_(None)
    if kind == "all":
        return (Message.folder_kind != "junk") & not_snoozed
    if kind == "starred":
        # 与 Gmail 一致：垃圾邮件中的星标邮件不显示在「已加星标」里
        return (Message.is_starred.is_(True)) & (Message.folder_kind != "junk") & not_snoozed
    label_id = _label_id_of(kind)
    if label_id is not None:
        return Message.id.in_(
            select(MessageLabel.message_id).where(MessageLabel.label_id == label_id)
        ) & (Message.folder_kind != "junk") & not_snoozed
    if kind == "sent":
        return Message.folder_kind == "sent"
    return (Message.folder_kind == kind) & not_snoozed


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


def _apply_cookie(response, value: Optional[str]) -> None:
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


def delete_messages(request: Request, db: Session, messages: list[Message]) -> int:
    """删除邮件：本地立即删除，Gmail 上的删除写入 outbox 后台执行。"""
    if not messages:
        return 0
    audit.record_request(request, "mail_deleted", audit.summarize_subjects([m.subject for m in messages]))
    touched = _enqueue_remote_delete(db, messages)
    deleted = _delete_local_messages(db, messages)
    outbox.kick(touched)
    return deleted


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


def nav_payload(request: Request) -> dict:
    """左侧导航：账号、各文件夹未读数、标签、草稿数。"""
    nav = sidebar_data(request)
    return {
        "accounts": [
            {
                "id": a.id,
                "email": a.email,
                "name": a.name,
                "color": a.color,
                "unread_inbox": a.unread_inbox,
                "unread_junk": a.unread_junk,
                "group": a.group,
            }
            for a in nav.accounts
        ],
        "unread_inbox": nav.unread_inbox,
        "unread_junk": nav.unread_junk,
        "drafts": nav.drafts,
        "snoozed": nav.snoozed,
        "scheduled": nav.scheduled,
        "labels": [
            {"id": lb.id, "account_id": lb.account_id, "name": lb.name, "short": lb.short}
            for lb in nav.labels
        ],
        "preferred_account_id": nav.preferred_account_id,
    }


@router.get("/nav", name="messages.nav")
def nav(request: Request):
    """左侧导航数据（未读数等），实时推送收到新邮件时前端只刷新这一部分。"""
    return nav_payload(request)


@router.get("/remote-search", name="messages.remote_search")
def remote_search(q: str = Query(..., min_length=1, max_length=500), account_id: Optional[int] = Query(None)):
    """在 Gmail 服务器上搜索（包括本地没有同步的邮件）。普通 def：IMAP 是阻塞调用，放进线程池执行。"""
    return remote_search_service.search(q, [account_id] if account_id else None)


def _list_item(m: Message, snippets, attachments_by_message, message_labels, thread_stats) -> dict:
    peer_header = m.to_addr if m.folder_kind == "sent" else m.from_addr
    stats = thread_stats.get(int(m.id)) or {}
    atts = attachments_by_message.get(int(m.id), [])
    return {
        "id": int(m.id),
        "account_id": int(m.account_id),
        "folder_kind": m.folder_kind,
        "subject": m.subject or "",
        "peer": ser.addr_name(peer_header),
        "peer_full": peer_header or "",
        "date": ser.iso(m.date),
        "is_read": bool(m.is_read),
        "is_starred": bool(m.is_starred),
        "has_attachments": bool(m.has_attachments),
        "snippet": snippets.get(int(m.id), ""),
        "attachments": [{"name": a.display_name or a.filename, "mime_type": (a.mime_type or "").lower()} for a in atts],
        "labels": message_labels.get(int(m.id), []),
        "thread_count": int(stats.get("count", 1)),
        "thread_unread": int(stats.get("unread", 0)),
        "snoozed_until": ser.iso_utc(m.snoozed_until),
        "otp_code": m.otp_code,
    }


@router.get("", name="messages.list")
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
        # 第一页，或只带了页码的旧链接/操作后刷新：按 OFFSET 兜底
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

    # 「第 first-last 封，共 total 封」
    first_index = (page - 1) * page_size + 1 if total else 0
    last_index = first_index + len(messages) - 1 if first_index else 0

    response = JSONResponse({
        "items": [
            _list_item(m, snippets, attachments_by_message, message_labels, thread_stats) for m in messages
        ],
        "kind": kind,
        "kind_label": kind_label,
        "current_label": {"id": int(current_label.id), "name": current_label.name} if current_label else None,
        "account_id": selected_account_id,
        "has_accounts": bool(accounts),
        "q": q_clean,
        "page": page,
        "pages": pages,
        "page_size": page_size,
        "page_size_options": list(PAGE_SIZE_OPTIONS),
        "total": total,
        "unread_total": unread_total,
        "first_index": first_index,
        "last_index": last_index,
        # 较新 / 较旧翻页带上游标（当前页首 / 尾邮件的 date~id），服务端无需 OFFSET
        "next_cursor": Cursor.of(messages[-1]).encode() if messages else None,
        "prev_cursor": Cursor.of(messages[0]).encode() if messages else None,
        "conversation": conversation,
        "any_busy": any(is_account_busy(int(a.id)) for a in accounts),
        "unread_only": unread,
    })
    _apply_cookie(response, cookie_to_persist)
    return response


class IdsBody(BaseModel):
    ids: list[int] = []


class ActionBody(IdsBody):
    action: str


@router.post("/actions", name="messages.actions")
def batch_action(request: Request, body: ActionBody, db: Session = Depends(get_db)):
    """批量操作选中的邮件：read（标为已读）/ unread（标为未读）/ delete（删除）。"""
    messages = _load_for_action(request, db, body.ids)
    if body.action == "read":
        affected = _toggle_messages_read_state(db, messages, is_read=True)
    elif body.action == "unread":
        affected = _toggle_messages_read_state(db, messages, is_read=False)
    elif body.action == "delete":
        affected = delete_messages(request, db, messages)
    else:
        raise HTTPException(status_code=400, detail="Unknown action")
    logger.info("batch-%s selected=%s affected=%s", body.action, len(body.ids), affected)
    return {"ok": True, "affected": affected}


class SnoozeBody(IdsBody):
    # 延后到的时间（浏览器给出的毫秒时间戳）；为空表示取消延后
    until_ms: Optional[int] = None


@router.post("/snooze", name="messages.snooze")
def snooze(request: Request, body: SnoozeBody, db: Session = Depends(get_db)):
    """延后（稍后提醒）选中的邮件；会话视图下整个会话一起延后。"""
    messages = _load_for_action(request, db, body.ids)
    until = None
    if body.until_ms is not None:
        until = datetime.fromtimestamp(body.until_ms / 1000, tz=timezone.utc).replace(tzinfo=None)
        if until <= utcnow():
            raise HTTPException(status_code=400, detail="请选择一个将来的时间")
    for m in messages:
        m.snoozed_until = until
    db.commit()
    return {"ok": True, "affected": len(messages), "until": ser.iso_utc(until)}


class ViewBody(BaseModel):
    kind: str = "inbox"
    account_id: Optional[int] = None
    q: Optional[str] = None


@router.post("/mark-all-read", name="messages.mark_all_read")
def mark_all_read(body: ViewBody, db: Session = Depends(get_db)):
    """把当前视图（文件夹 + 账号 + 搜索）下的全部未读邮件标为已读。

    本地数据库立即更新；Gmail 服务器上的已读标记随后尽力同步（失败只记日志，不回滚本地）。
    """
    kind = _normalize_kind(body.kind)
    stmt = select(Message).where(Message.is_read.is_(False))
    stmt = _apply_kind_account_filters(stmt, kind, body.account_id)
    search_filter = message_search_filter(body.q)
    if search_filter is not None:
        stmt = stmt.where(search_filter)

    targets = db.execute(stmt).scalars().all()
    affected = _toggle_messages_read_state(db, targets, is_read=True)
    logger.info("mark-all-read kind=%s account_id=%s affected=%s", kind, body.account_id, affected)
    return {"ok": True, "affected": affected}


class ConversationBody(BaseModel):
    on: bool = True


@router.post("/conversation-view", name="messages.conversation_view")
def set_conversation_view(body: ConversationBody):
    """开启 / 关闭会话视图（记在 cookie 中）。"""
    response = JSONResponse({"ok": True, "conversation": body.on})
    response.set_cookie(
        threads.CONVERSATION_COOKIE,
        "1" if body.on else "0",
        max_age=COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
    )
    return response


def _get_message(db: Session, message_id: int) -> Message:
    message = db.get(Message, message_id)
    if message is None:
        raise HTTPException(status_code=404, detail="Message not found")
    return message


class StarBody(BaseModel):
    starred: Optional[bool] = None


@router.post("/{message_id}/star", name="messages.star")
def toggle_star(message_id: int, body: StarBody, db: Session = Depends(get_db)):
    """加 / 取消星标。``starred`` 为空时切换。"""
    message = _get_message(db, message_id)
    value = body.starred if body.starred is not None else not message.is_starred
    _set_starred(db, [message], value)
    return {"id": message_id, "starred": value}


@router.post("/{message_id}/mark-unread", name="messages.mark_unread")
def mark_unread(request: Request, message_id: int, db: Session = Depends(get_db)):
    message = _get_message(db, message_id)
    targets = threads.expand(db, [message]) if threads.conversation_mode(request) else [message]
    _toggle_messages_read_state(db, targets, is_read=False)
    return {"ok": True}


@router.post("/{message_id}/delete", name="messages.delete_one")
def delete_one(request: Request, message_id: int, db: Session = Depends(get_db)):
    message = db.execute(
        select(Message)
        .options(selectinload(Message.attachments))
        .where(Message.id == message_id)
    ).scalar_one_or_none()
    if message is None:
        raise HTTPException(status_code=404, detail="Message not found")
    targets = threads.expand(db, [message]) if threads.conversation_mode(request) else [message]
    delete_messages(request, db, targets)
    logger.info("delete-one message_id=%s account_id=%s mode=async_remote", message_id, message.account_id)
    return {"ok": True}


class LabelsBody(BaseModel):
    label_ids: list[int] = []


@router.post("/{message_id}/labels", name="messages.set_labels")
def set_labels(request: Request, message_id: int, body: LabelsBody, db: Session = Depends(get_db)):
    """设置邮件（会话视图下为整个会话）的 Gmail 标签：勾选的加上，未勾选的去掉。"""
    message = _get_message(db, message_id)
    targets = threads.expand(db, [message]) if threads.conversation_mode(request) else [message]
    account_labels = {
        int(label.id): label
        for label in db.execute(select(Label).where(Label.account_id == message.account_id)).scalars()
    }
    wanted = {i for i in body.label_ids if i in account_labels}
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
    return {"ok": True, "labels": _thread_labels(db, targets)}


def _thread_labels(db: Session, thread: list[Message]) -> list[dict]:
    """会话中所有邮件的标签（去重，按名称排序）。"""
    merged: dict[int, dict] = {}
    for items in gmail_labels.labels_by_message(db, [int(m.id) for m in thread]).values():
        for item in items:
            merged[item["id"]] = item
    return sorted(merged.values(), key=lambda item: item["name"])


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


def _list_back_url(back: Optional[str], folder_kind: str) -> str:
    """详情页「返回」的目标：打开邮件前所在的列表（保留账号、页码、筛选、搜索）。

    只接受站内的邮件列表地址；没有或不合法时回到该文件夹列表首页。
    """
    if back and is_safe_redirect(back) and back.startswith("/messages/?"):
        return back
    return f"/messages/?kind={_normalize_kind(folder_kind)}"


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


def _thread_item(m: Message, expanded: bool) -> dict:
    own = m.account.email if m.account else ""
    return {
        "id": int(m.id),
        "account_id": int(m.account_id),
        "account_email": own,
        "folder_kind": m.folder_kind,
        "subject": m.subject or "",
        "from_name": ser.addr_name(m.from_addr),
        "from_email": ser.addr_email(m.from_addr),
        "from_full": ser.addr_list(m.from_addr),
        "to_full": ser.addr_list(m.to_addr or own),
        "cc_full": ser.addr_list(m.cc_addr) if m.cc_addr else "",
        "to_short": ser.recipients(m.to_addr, own) or own,
        "cc_short": ser.recipients(m.cc_addr, own) if m.cc_addr else "",
        "date": ser.iso(m.date),
        "snippet": ser.snippet(m.body_text),
        "is_read": bool(m.is_read),
        "is_starred": bool(m.is_starred),
        "expanded": expanded,
        # 正文中有被拦下的外部图片（前端显示「显示图片」提示条）；默认就加载外部图片时不拦
        "remote_images": body_has_remote_images(m.body_html) and not remote_images_allowed(),
        "otp_code": m.otp_code,
        "attachments": [ser.attachment(a) for a in sorted(m.attachments, key=lambda a: (a.is_inline, a.id))],
    }


@router.get("/{message_id}", name="messages.detail")
def view_message(
    request: Request,
    message_id: int,
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

    back_url = _list_back_url(back, message.folder_kind)
    sender_email = _normalize_sender_email(message.from_addr)
    sender_blocked = bool(sender_email) and db.execute(
        select(BlockedSender.id).where(
            BlockedSender.account_id == message.account_id,
            BlockedSender.sender_email == sender_email,
        )
    ).first() is not None

    return {
        "id": int(message.id),
        "account_id": int(message.account_id),
        "subject": message.subject or "",
        "folder_kind": message.folder_kind,
        "is_starred": bool(message.is_starred),
        "snoozed_until": ser.iso_utc(message.snoozed_until),
        "thread": [_thread_item(m, int(m.id) in expanded_ids) for m in thread],
        "thread_labels": _thread_labels(db, thread),
        "account_labels": [
            {"id": int(lb.id), "name": lb.name}
            for lb in db.execute(
                select(Label).where(Label.account_id == message.account_id).order_by(Label.name)
            ).scalars()
        ],
        "sender_email": sender_email,
        "sender_blocked": sender_blocked,
        "back_url": back_url,
        "position": _position_in_list(db, message, back_url, conversation),
        "conversation": conversation,
    }


@body_router.get("/{message_id}/body", response_class=HTMLResponse, name="messages.body")
def message_body(
    request: Request,
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
    return body_document_response(
        row.body_html, row.body_text,
        allow_remote_images=remote_images_allowed(images), origin=request_origin(request),
    )
