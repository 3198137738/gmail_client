"""多连接并行下载：首次同步和历史回填时，用同一账号的几条额外 IMAP 连接同时取不同批次。

单条连接下载邮件时，带宽和每条命令的往返延迟都只用到一份；Gmail 允许每个账号
同时 15 条连接，这里按 ``SYNC_PARALLEL_CONNECTIONS`` 再开几条，把待下载的批次分给它们。
结果按完成顺序交给调用方，调用方边收边解析、入库，下载和解析也就重叠起来了。

* 连接按需打开：第一次有 ≥ 2 批要下载时才建立，增量同步的小批量不付登录开销。
* 内存有上限：结果队列最多存 ``size`` 批，工作线程取满后等待，调用方慢时不会堆积。
* 连接全部打不开（限流、网络故障）时不报错：调用方拿到 ``PoolUnavailable`` 后改用自己的
  主连接顺序下载，同步照常完成。
* 调用方中途停止（限流、断线）：``stop()`` 让工作线程做完手头一批就退出并关闭连接。
"""
from __future__ import annotations

import logging
import queue
import threading
from collections import deque
from typing import Callable, Iterator, Optional

logger = logging.getLogger(__name__)

# 等待工作线程结果的轮询间隔：期间检查是否所有连接都已失效
_WAIT_SEC = 1.0


class PoolUnavailable(Exception):
    """所有并行连接都打不开；调用方应改用主连接顺序下载 ``remaining`` 里的批次。"""

    def __init__(self, remaining: Optional[list[list[int]]] = None) -> None:
        super().__init__("no parallel IMAP connection available")
        self.remaining = remaining or []


class FetchPool:
    """为一个账号管理若干条下载连接。``connect()`` 返回已登录的 IMAPClient，由调用方提供。"""

    def __init__(self, connect: Callable[[], object], *, size: int, close: Optional[Callable] = None) -> None:
        self._connect = connect
        self._close = close or _default_close
        self.size = max(1, int(size))
        self._clients: list = []
        self._folders: dict[int, str] = {}        # id(client) -> 当前选中的文件夹
        self._lock = threading.Lock()
        self._closed = False
        self.connections_used = 0                 # 实际打开过的连接数（用于日志）

    # ---- 连接管理 ------------------------------------------------------------
    def _ensure_connections(self, wanted: int) -> list:
        """打开到 ``wanted`` 条连接（至少已有的那些）。打不开的记日志跳过。"""
        with self._lock:
            if self._closed:
                return []
            missing = min(wanted, self.size) - len(self._clients)
            for _ in range(max(0, missing)):
                try:
                    client = self._connect()
                except Exception as exc:
                    logger.warning("Parallel IMAP connection failed: %s", exc)
                    break
                self._clients.append(client)
                self.connections_used = max(self.connections_used, len(self._clients))
            return list(self._clients)

    def _select(self, client, folder: str) -> None:
        if self._folders.get(id(client)) != folder:
            client.select_folder(folder, readonly=True)
            self._folders[id(client)] = folder

    def _drop(self, client) -> None:
        with self._lock:
            if client in self._clients:
                self._clients.remove(client)
            self._folders.pop(id(client), None)
        self._close(client)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            clients, self._clients = self._clients, []
            self._folders.clear()
        for client in clients:
            self._close(client)

    # ---- 下载 ----------------------------------------------------------------
    def fetch_batches(self, folder: str, batches: list[list[int]], items: list[str]) -> Iterator[tuple[list[int], object]]:
        """并行下载各批，按完成顺序产出 ``(chunk, fetched_dict)`` 或 ``(chunk, exception)``。

        连接全部不可用时抛 ``PoolUnavailable``（可能在产出若干批之后，剩余批由调用方处理，
        异常的 ``remaining`` 属性是还没下载的批次）。调用方中途 ``break`` 会停止全部工作线程。
        """
        if not batches:
            return
        clients = self._ensure_connections(len(batches))
        if not clients:
            raise PoolUnavailable(remaining=list(batches))

        pending: deque[list[int]] = deque(batches)
        results: queue.Queue = queue.Queue(maxsize=self.size)
        stop = threading.Event()
        state_lock = threading.Lock()
        alive = [len(clients)]

        def worker(client) -> None:
            try:
                while not stop.is_set():
                    with state_lock:
                        if not pending:
                            return
                        chunk = pending.popleft()
                    try:
                        self._select(client, folder)
                        fetched = client.fetch(chunk, items)
                    except Exception as exc:    # 分类（限流 / 单封失败）交给调用方
                        results.put((chunk, exc))
                        if _connection_broken(exc):
                            self._drop(client)
                            return
                        continue
                    results.put((chunk, fetched))
            finally:
                with state_lock:
                    alive[0] -= 1

        threads = [threading.Thread(target=worker, args=(c,), name="gmail-fetch", daemon=True) for c in clients]
        for t in threads:
            t.start()
        produced = 0
        try:
            while produced < len(batches):
                try:
                    chunk, payload = results.get(timeout=_WAIT_SEC)
                except queue.Empty:
                    with state_lock:
                        dead = alive[0] <= 0 and results.empty()
                        leftover = list(pending) if dead else None
                    if dead:
                        if leftover:
                            raise PoolUnavailable(remaining=leftover)
                        return
                    continue
                produced += 1
                yield chunk, payload
        finally:
            stop.set()
            for t in threads:
                t.join(timeout=30)


def _connection_broken(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(s in text for s in ("socket", "eof", "connection", "broken pipe", "timed out", "abort"))


def _default_close(client) -> None:
    try:
        client.logout()
    except Exception:
        try:
            client.shutdown()
        except Exception:
            pass
