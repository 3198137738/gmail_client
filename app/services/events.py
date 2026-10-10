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


def publish_new_mail(account_id: int, *, inbox: int = 0, sent: int = 0) -> None:
    """Convenience helper announcing newly stored messages for an account."""
    event_bus.publish(
        {
            "type": "new_mail",
            "account_id": int(account_id),
            "inbox": int(inbox),
            "sent": int(sent),
        }
    )
