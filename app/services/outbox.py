"""远程操作 outbox：把对 Gmail 的修改（删除、标记、移动）持久化后由后台重试执行。

为什么需要它
------------
本地修改是立即生效的（界面不必等网络），远程操作之前直接丢进线程池「尽力而为」：
账号锁被同步占用、网络抖动或进程重启时，远程操作就悄悄丢了，本地和 Gmail 永久不一致
（例如本地已删除、Gmail 里还在，且 UID <= last_uid，之后也不会再同步回来）。

现在调用方在**修改本地数据的同一个事务里** :func:`enqueue` 一条记录，提交后
:func:`kick` 让后台立即执行；失败按指数退避（30 秒起，最长 1 小时）一直重试到成功。
调度器每分钟扫一次到期的任务，进程重启后也会继续执行。

UID 只在 UIDVALIDITY 不变时有效：执行前若发现文件夹的 UIDVALIDITY 已变化，丢弃该任务，
避免作用到服务器上另一封邮件。
"""
from __future__ import annotations

import logging
import threading
from datetime import timedelta
from typing import Iterable, Optional

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..database import session_scope
from ..models import PendingOp, SyncState
from ..timeutil import utcnow
from .background import submit_bg
from .imap_ops import (
    delete_imap_messages,
    move_imap_messages,
    update_gmail_labels,
    update_imap_flags,
)

logger = logging.getLogger(__name__)

OP_DELETE = "delete"
OP_ADD_FLAGS = "add_flags"
OP_REMOVE_FLAGS = "remove_flags"
OP_MOVE = "move"
OP_ADD_LABEL = "add_label"
OP_REMOVE_LABEL = "remove_label"
_OPS = {OP_DELETE, OP_ADD_FLAGS, OP_REMOVE_FLAGS, OP_MOVE, OP_ADD_LABEL, OP_REMOVE_LABEL}
# 作用于同一状态的操作（同一个标记 / 标签的加和减）：同一封邮件只有最后一次有意义
_SUPERSEDE_FAMILIES = (
    (OP_ADD_FLAGS, OP_REMOVE_FLAGS),
    (OP_ADD_LABEL, OP_REMOVE_LABEL),
)

BASE_BACKOFF_SEC = 30
MAX_BACKOFF_SEC = 3600
# 连续失败达到这个次数时记一条 error 日志（之后仍继续重试）
ALERT_AFTER_ATTEMPTS = 10
# 每轮最多处理的任务数，避免单个账号长时间占用线程
BATCH_LIMIT = 50

_mutex = threading.Lock()
_running: set[int] = set()
_rerun: set[int] = set()


def _encode_uids(uids: Iterable[int]) -> str:
    return ",".join(str(int(u)) for u in sorted(set(uids)))


def _decode_uids(raw: str) -> list[int]:
    return [int(part) for part in (raw or "").split(",") if part.strip()]


def enqueue(
    db: Session,
    account_id: int,
    folder: str,
    op: str,
    uids: Iterable[int],
    arg: Optional[str] = None,
) -> None:
    """在调用方的会话里加入一条远程操作（由调用方提交事务，之后调用 :func:`kick`）。"""
    if op not in _OPS:
        raise ValueError(f"unknown op {op!r}")
    encoded = _encode_uids(uids)
    if not encoded:
        return
    _supersede(db, account_id, folder, op, set(_decode_uids(encoded)), arg)
    uidvalidity = db.execute(
        select(SyncState.uidvalidity).where(
            SyncState.account_id == account_id, SyncState.folder == folder
        )
    ).scalar()
    db.add(
        PendingOp(
            account_id=int(account_id),
            folder=folder,
            uidvalidity=uidvalidity,
            op=op,
            uids=encoded,
            arg=arg,
            next_attempt_at=utcnow(),
        )
    )


def _supersede(
    db: Session, account_id: int, folder: str, op: str, uids: set[int], arg: Optional[str]
) -> None:
    """从排队中的旧操作里去掉这些邮件：它们对同一状态的修改已被这次操作取代。

    否则旧操作失败退避后再重试成功，会覆盖用户之后的操作（例如先标已读失败、
    再标未读成功，30 秒后已读重试成功，最终变成已读）。
    """
    family = next((f for f in _SUPERSEDE_FAMILIES if op in f), None)
    if family is None:
        return
    rows = db.execute(
        select(PendingOp).where(
            PendingOp.account_id == account_id,
            PendingOp.folder == folder,
            PendingOp.op.in_(family),
            PendingOp.arg == arg,
        )
    ).scalars().all()
    for row in rows:
        old_uids = _decode_uids(row.uids)
        remaining = [u for u in old_uids if u not in uids]
        if len(remaining) == len(old_uids):
            continue
        if remaining:
            row.uids = _encode_uids(remaining)
        else:
            db.delete(row)


def kick(account_ids: Iterable[int]) -> None:
    """事务提交后调用：让后台立即处理这些账号的待执行操作。"""
    for account_id in sorted(set(int(a) for a in account_ids)):
        submit_bg(process_account, account_id)


def pending_uids(
    db: Session,
    account_id: int,
    folder: str,
    ops: Optional[Iterable[str]] = None,
    arg: Optional[str] = None,
) -> set[int]:
    """该文件夹中还有远程操作在排队的 UID。

    同步时用来避免「本地已删、远程还没删」的邮件被重新下载，以及
    「本地刚标记已读、还没推到服务器」的状态被服务器旧状态覆盖。
    """
    stmt = select(PendingOp.uids).where(
        PendingOp.account_id == account_id, PendingOp.folder == folder
    )
    if ops is not None:
        stmt = stmt.where(PendingOp.op.in_(list(ops)))
    if arg is not None:
        stmt = stmt.where(PendingOp.arg == arg)
    result: set[int] = set()
    for raw in db.execute(stmt).scalars():
        result.update(_decode_uids(raw))
    return result


def drop_folder_ops(db: Session, account_id: int, folder: str) -> int:
    """UIDVALIDITY 变化时丢弃该文件夹的全部待执行操作（旧 UID 已失效）。"""
    return db.execute(
        delete(PendingOp).where(PendingOp.account_id == account_id, PendingOp.folder == folder)
    ).rowcount or 0


def _execute(op: PendingOp) -> Optional[str]:
    """执行一条操作。成功返回 None，失败返回错误描述。"""
    uids = _decode_uids(op.uids)
    if not uids:
        return None
    account_id = int(op.account_id)
    if op.op == OP_DELETE:
        result = delete_imap_messages(account_id, [(op.folder, uids)])
    elif op.op == OP_ADD_FLAGS:
        result = update_imap_flags(account_id, [(op.folder, uids)], add_flags=[op.arg.encode()])
    elif op.op == OP_REMOVE_FLAGS:
        result = update_imap_flags(account_id, [(op.folder, uids)], remove_flags=[op.arg.encode()])
    elif op.op == OP_MOVE:
        result = move_imap_messages(account_id, op.folder, uids, target=op.arg)
    elif op.op == OP_ADD_LABEL:
        result = update_gmail_labels(account_id, op.folder, uids, add=[op.arg])
    elif op.op == OP_REMOVE_LABEL:
        result = update_gmail_labels(account_id, op.folder, uids, remove=[op.arg])
    else:
        return f"unknown op {op.op}"
    if result.get("ok"):
        return None
    folder_error = (result.get("folders") or {}).get(op.folder, {}).get("error")
    return str(folder_error or result.get("error") or "failed")


def _process_due_batch(account_id: int) -> int:
    """处理一批到期任务，返回处理条数。"""
    now = utcnow()
    with session_scope() as db:
        ops = db.execute(
            select(PendingOp)
            .where(PendingOp.account_id == account_id, PendingOp.next_attempt_at <= now)
            .order_by(PendingOp.id)
            .limit(BATCH_LIMIT)
        ).scalars().all()
        validity = {
            folder: value
            for folder, value in db.execute(
                select(SyncState.folder, SyncState.uidvalidity).where(
                    SyncState.account_id == account_id
                )
            ).all()
        }
        for op in ops:
            db.expunge(op)

    for op in ops:
        current = validity.get(op.folder)
        if op.uidvalidity and current and op.uidvalidity != current:
            logger.warning(
                "Dropping stale outbox op id=%s account=%s folder=%s: UIDVALIDITY changed",
                op.id, account_id, op.folder,
            )
            error = None
        else:
            try:
                error = _execute(op)
            except Exception as exc:  # imap_ops 本身不抛异常，这里只是兜底
                logger.exception("Outbox op crashed id=%s", op.id)
                error = exc.__class__.__name__

        with session_scope() as db:
            row = db.get(PendingOp, op.id)
            if row is None:
                continue
            if error is None:
                db.delete(row)
                continue
            row.attempts += 1
            delay = min(MAX_BACKOFF_SEC, BASE_BACKOFF_SEC * 2 ** (row.attempts - 1))
            row.next_attempt_at = utcnow() + timedelta(seconds=delay)
            row.last_error = error[:500]
            log = logger.error if row.attempts == ALERT_AFTER_ATTEMPTS else logger.warning
            log(
                "Outbox op failed id=%s account=%s op=%s folder=%s attempts=%s retry_in=%ss: %s",
                row.id, account_id, row.op, row.folder, row.attempts, delay, error,
            )
    return len(ops)


def process_account(account_id: int) -> None:
    """执行该账号所有到期的远程操作。同一账号同时只有一个线程在处理。"""
    with _mutex:
        if account_id in _running:
            _rerun.add(account_id)  # 处理中又有新任务：当前线程结束前再扫一遍
            return
        _running.add(account_id)
    try:
        while True:
            try:
                handled = _process_due_batch(account_id)
            except Exception:
                logger.exception("Outbox processing failed account=%s", account_id)
                handled = 0
            with _mutex:
                again = account_id in _rerun or handled >= BATCH_LIMIT
                _rerun.discard(account_id)
                if not again:
                    _running.discard(account_id)
                    return
    except BaseException:
        with _mutex:
            _running.discard(account_id)
        raise


def process_all_due() -> None:
    """调度器定时调用：为有到期任务的账号安排处理。"""
    try:
        with session_scope() as db:
            account_ids = db.execute(
                select(PendingOp.account_id)
                .where(PendingOp.next_attempt_at <= utcnow())
                .distinct()
            ).scalars().all()
    except Exception:
        logger.exception("Failed to scan outbox")
        return
    kick(account_ids)
