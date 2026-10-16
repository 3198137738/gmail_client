"""会话视图（Gmail 式）：同一会话（X-GM-THRID 相同）的往来邮件在列表中合成一行。

列表中每个会话只显示「代表邮件」——符合当前筛选条件的邮件里最新的一封。
这只是在原有列表查询上多加一个 WHERE 条件（:func:`representative_condition`），
所以分页游标、总数、详情页的「第 N 封 / 较新 / 较旧」都无需改动。

会话按账号区分；垃圾邮件不与其他文件夹的邮件合并（与 Gmail 一致）。
没有会话 id 的邮件（非 Gmail、或历史邮件尚未补齐）各自单独成一个会话。
"""
from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from fastapi import Request
from sqlalchemy import and_, case, func, or_, select, tuple_
from sqlalchemy.orm import Session, selectinload

from ..models import Message
from . import pagination

# 是否使用会话视图，记在 cookie 中（默认开启，与 Gmail 一致）
CONVERSATION_COOKIE = "gc_conv"


def conversation_mode(request: Request) -> bool:
    return request.cookies.get(CONVERSATION_COOKIE) != "0"


# 垃圾邮件、已删除邮件与其他文件夹的邮件不合并成一个会话（与 Gmail 一致）
_SEPARATE_KINDS = ("junk", "trash")


def _bucket():
    return case((Message.folder_kind == "junk", 1), (Message.folder_kind == "trash", 2), else_=0)


def _bucket_of(folder_kind: str) -> int:
    return {"junk": 1, "trash": 2}.get(folder_kind, 0)


def _thread_key():
    # 没有会话 id 的邮件用 -id 作为独立的会话（Gmail 的会话 id 都是正数）
    return case((Message.thread_id.is_not(None), Message.thread_id), else_=-Message.id)


def representative_condition(conditions: Iterable):
    """只保留每个会话中符合 ``conditions`` 的最新一封。"""
    row_number = func.row_number().over(
        partition_by=(Message.account_id, _bucket(), _thread_key()),
        order_by=pagination.forward_order(),
    ).label("rn")
    ranked = select(Message.id.label("id"), row_number).where(*conditions).subquery()
    return Message.id.in_(select(ranked.c.id).where(ranked.c.rn == 1))


def _same_thread(message: Message):
    kind = message.folder_kind
    return and_(
        Message.account_id == message.account_id,
        Message.thread_id == message.thread_id,
        (Message.folder_kind == kind) if kind in _SEPARATE_KINDS else Message.folder_kind.not_in(_SEPARATE_KINDS),
    )


def thread_messages(db: Session, message: Message) -> list[Message]:
    """会话中的全部邮件，按时间从早到晚。"""
    if message.thread_id is None:
        return [message]
    rows = db.execute(
        select(Message)
        .options(selectinload(Message.attachments), selectinload(Message.account))
        .where(_same_thread(message))
        .order_by(*pagination.backward_order())
    ).scalars().all()
    return list(rows) or [message]


def expand(db: Session, messages: list[Message]) -> list[Message]:
    """把选中的（代表）邮件扩展为它们所在会话的全部邮件，用于删除 / 标记等批量操作。"""
    threaded = [m for m in messages if m.thread_id is not None]
    if not threaded:
        return messages
    members = db.execute(
        select(Message)
        .options(selectinload(Message.attachments))
        .where(or_(*[_same_thread(m) for m in threaded]))
    ).scalars().all()
    seen = {int(m.id): m for m in messages}
    for member in members:
        seen.setdefault(int(member.id), member)
    return list(seen.values())


def thread_stats(db: Session, messages: list[Message]) -> dict[int, dict]:
    """列表当前页每个代表邮件所在会话的邮件数、是否有未读、是否有星标。"""
    keys = {
        (int(m.account_id), int(m.thread_id), _bucket_of(m.folder_kind))
        for m in messages if m.thread_id is not None
    }
    if not keys:
        return {}
    rows = db.execute(
        select(
            Message.account_id,
            Message.thread_id,
            _bucket(),
            func.count(Message.id),
            func.sum(case((Message.is_read.is_(False), 1), else_=0)),
            func.sum(case((Message.is_starred.is_(True), 1), else_=0)),
        )
        .where(tuple_(Message.account_id, Message.thread_id).in_([(a, t) for a, t, _ in keys]))
        .group_by(Message.account_id, Message.thread_id, _bucket())
    ).all()
    by_key = defaultdict(dict)
    for account_id, thread_id, bucket, count, unread, starred in rows:
        by_key[(int(account_id), int(thread_id), int(bucket))] = {
            "count": int(count), "unread": int(unread or 0), "starred": int(starred or 0),
        }
    result: dict[int, dict] = {}
    for m in messages:
        if m.thread_id is None:
            continue
        stats = by_key.get((int(m.account_id), int(m.thread_id), _bucket_of(m.folder_kind)))
        if stats:
            result[int(m.id)] = stats
    return result
