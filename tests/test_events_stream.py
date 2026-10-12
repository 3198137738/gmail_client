"""SSE：订阅只在生成器真正运行时建立，结束后一定取消。"""
from __future__ import annotations

import asyncio

from app.routes import events as events_routes
from app.services.events import event_bus


class _Request:
    async def is_disconnected(self) -> bool:
        return False


def test_unstarted_stream_does_not_leak_subscriber():
    async def run():
        before = event_bus.subscriber_count
        response = await events_routes.stream(_Request())
        assert event_bus.subscriber_count == before        # 还没开始输出：没有订阅

        body = response.body_iterator
        first = await body.__anext__()
        assert '"ready"' in first
        assert event_bus.subscriber_count == before + 1
        await body.aclose()                                 # 客户端断开
        assert event_bus.subscriber_count == before

    asyncio.run(run())
