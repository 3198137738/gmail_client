"""Server-Sent Events stream pushing real-time mailbox updates to browsers.

Clients open ``EventSource('/events/stream')`` once; the server keeps the
connection open and emits a ``new_mail`` event (with per-folder counts) every
time an IMAP IDLE worker or a sync stores fresh messages. A periodic comment
ping keeps proxies from closing the idle connection.
"""
from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from ..services.events import event_bus

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/events", tags=["events"])

# Seconds between keep-alive comments when no real events flow.
_PING_INTERVAL = 20


def _format_event(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


@router.get("/stream", name="events.stream")
async def stream(request: Request):
    async def event_generator():
        # 在生成器里订阅：客户端在响应开始前就断开时生成器不会运行，
        # 若在外面订阅，finally 里的取消订阅永远不会执行，订阅者就泄漏了
        subscriber = event_bus.subscribe()
        try:
            # Initial handshake so the client can flip its status to "connected".
            yield _format_event({"type": "ready"})
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(subscriber.get(), timeout=_PING_INTERVAL)
                    yield _format_event(event)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        finally:
            event_bus.unsubscribe(subscriber)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # Disable proxy buffering (nginx) so events are flushed immediately.
            "X-Accel-Buffering": "no",
        },
    )
