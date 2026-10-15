"""In-process event bus bridging worker threads to SSE clients.

Background work (IMAP IDLE workers, the scheduler, manual sync) runs in plain
threads, while Server-Sent Events handlers are async coroutines living on the
uvicorn event loop. This module is the thread-safe bridge: publishers call
:func:`publish` from any thread, and each connected SSE client owns a
:class:`Subscriber` whose :class:`asyncio.Queue` is fed via
``loop.call_soon_threadsafe`` so cross-thread hand-off is safe.

The whole design assumes a **single application process** (one uvicorn worker),
which is how this app is deployed. With multiple workers an external broker
(Redis pub/sub, etc.) would be required instead.
"""
from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any

logger = logging.getLogger(__name__)

# Per-subscriber queue cap. New mail events are tiny and consumers drain almost
# immediately; if a slow/stuck client lets the queue fill we drop the oldest
# event rather than grow without bound.
_QUEUE_MAXSIZE = 100


class Subscriber:
    """A single SSE connection's mailbox, bound to the serving event loop."""

    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)

    def _enqueue(self, event: dict[str, Any]) -> None:
        if self._queue.full():
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            pass

    def push(self, event: dict[str, Any]) -> None:
        """Thread-safe: schedule ``event`` onto the owning loop."""
        try:
            self._loop.call_soon_threadsafe(self._enqueue, event)
        except RuntimeError:
            # Loop already closed (client gone / shutdown) - ignore.
            pass

    async def get(self) -> dict[str, Any]:
        return await self._queue.get()


class EventBus:
    def __init__(self) -> None:
        self._subscribers: set[Subscriber] = set()
        self._lock = threading.Lock()

    def subscribe(self) -> Subscriber:
        sub = Subscriber(asyncio.get_running_loop())
        with self._lock:
            self._subscribers.add(sub)
        return sub

    def unsubscribe(self, sub: Subscriber) -> None:
        with self._lock:
            self._subscribers.discard(sub)

    def publish(self, event: dict[str, Any]) -> None:
        """Fan ``event`` out to every connected subscriber. Never raises."""
        with self._lock:
            targets = list(self._subscribers)
        for sub in targets:
            try:
                sub.push(event)
            except Exception:
                logger.debug("Failed to push event to a subscriber", exc_info=True)

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)


event_bus = EventBus()


PREVIEW_COUNT = 3


def new_mail_preview(account_id: int, count: int, message_ids=None) -> list[dict]:
    """新到收件箱邮件的摘要（最多 3 封，最新的在前），用于浏览器桌面通知。

    message_ids 为空时取该账号收件箱中最新入库的 count 封。
    """
    if count <= 0:
        return []
    from sqlalchemy import select

    from .. import serializers as ser
    from ..database import session_scope
    from ..models import Message

    stmt = select(Message.id, Message.from_addr, Message.subject, Message.otp_code).where(
        Message.account_id == account_id, Message.folder_kind == "inbox"
    )
    if message_ids is not None:
        stmt = stmt.where(Message.id.in_(list(message_ids)))
    with session_scope() as db:
        rows = db.execute(stmt.order_by(Message.id.desc()).limit(min(count, PREVIEW_COUNT))).all()
    return [
        {"id": int(mid), "from": ser.addr_name(from_addr), "subject": subject or "", "otp_code": otp_code}
        for mid, from_addr, subject, otp_code in rows
    ]


def publish_new_mail(
    account_id: int, *, inbox: int = 0, sent: int = 0, junk: int = 0, preview: list[dict] | None = None
) -> None:
    """Convenience helper announcing newly stored messages for an account."""
    event_bus.publish(
        {
            "type": "new_mail",
            "account_id": int(account_id),
            "inbox": int(inbox),
            "sent": int(sent),
            "junk": int(junk),
            "preview": preview or [],
        }
    )


def publish_mail_changed(account_id: int, folder_kind: str) -> None:
    """服务器上的已读状态或邮件删除同步到了本地，通知页面刷新列表 / 未读数（不算新邮件）。"""
    event_bus.publish(
        {"type": "mail_changed", "account_id": int(account_id), "kind": str(folder_kind)}
    )


def publish_sync_done(account_id: int) -> None:
    """一次整账号同步结束（无论有无新邮件），页面据此刷新「正在同步」状态。"""
    event_bus.publish({"type": "sync_done", "account_id": int(account_id)})


def publish_purge_done(*, senders: int, failed: int, purged: int) -> None:
    """后台批量永久删除屏蔽地址的邮件已结束，页面据此提示结果。"""
    event_bus.publish(
        {"type": "purge_done", "senders": int(senders), "failed": int(failed), "purged": int(purged)}
    )
