"""并行下载连接池：批次分给多条连接、结果与单连接一致、限流 / 单封失败 / 连接打不开时的行为。"""
from __future__ import annotations

import threading

import pytest
from imapclient.exceptions import IMAPClientError

from app.services import imap_service
from app.services.imap_pool import FetchPool, PoolUnavailable
from tests.helpers import create_account
from tests.test_backfill import FakeMailbox, _count, no_notify  # noqa: F401


@pytest.fixture(autouse=True)
def _small_batches(monkeypatch):
    monkeypatch.setattr(imap_service, "IMAP_FETCH_BATCH_SIZE", 10)
    imap_service._uid_failures.clear()
    imap_service._uid_transient.clear()
    yield
    imap_service._uid_failures.clear()
    imap_service._uid_transient.clear()


class Conn:
    """一条并行连接：同一个假邮箱的一个视图，记录自己取过哪些批。"""

    def __init__(self, box: FakeMailbox):
        self.box = box
        self.fetched: list[list[int]] = []
        self.closed = False
        self.selected = None
        self.fail_chunks_with: Exception | None = None
        self.bad_uids: set[int] = set()

    def select_folder(self, folder, readonly=True):
        self.selected = folder
        return self.box.select_folder(folder, readonly)

    def fetch(self, uids, items, modifiers=None):
        if "RFC822" in items:
            self.fetched.append(list(uids))
            if self.fail_chunks_with is not None:
                raise self.fail_chunks_with
            if self.bad_uids & set(uids):
                raise IMAPClientError("Some messages could not be FETCHed (Failure)")
        return self.box.fetch(uids, items, modifiers)

    def logout(self):
        self.closed = True


def _pool(box: FakeMailbox, size: int = 3, connect=None) -> tuple[FetchPool, list[Conn]]:
    conns: list[Conn] = []

    def default_connect():
        conn = Conn(box)
        conns.append(conn)
        return conn

    return FetchPool(connect or default_connect, size=size), conns


def test_batches_spread_over_connections(no_notify):
    acc = create_account()
    box = FakeMailbox(120)
    pool, conns = _pool(box)
    result = imap_service._ingest_uids(box, acc, "INBOX", "inbox", box.uids, pool=pool)
    pool.close()

    assert result.saved == 120 and not result.failed_uids and not result.interrupted
    assert _count(acc) == 120
    assert pool.connections_used == 3 and result.connections == 4
    fetched = sorted(uid for c in conns for chunk in c.fetched for uid in chunk)
    assert fetched == box.uids                       # 每封只下载一次
    assert sum(1 for c in conns if c.fetched) >= 2    # 确实分到了多条连接
    assert all(c.closed for c in conns)
    assert all(c.selected == "INBOX" for c in conns if c.fetched)
    assert result.download_sec >= 0 and result.parse_sec > 0 and result.db_sec > 0


def test_single_batch_does_not_open_connections(no_notify):
    acc = create_account()
    box = FakeMailbox(8)
    pool, conns = _pool(box)
    result = imap_service._ingest_uids(box, acc, "INBOX", "inbox", box.uids, pool=pool)
    assert result.saved == 8 and conns == [] and result.connections == 1


def test_pool_of_one_is_never_used(no_notify):
    acc = create_account()
    box = FakeMailbox(40)
    pool, conns = _pool(box, size=1)
    assert imap_service._ingest_uids(box, acc, "INBOX", "inbox", box.uids, pool=pool).saved == 40
    assert conns == []


def test_throttle_on_one_connection_defers_rest(no_notify):
    acc = create_account()
    box = FakeMailbox(60)
    throttled = threading.Event()

    def connect():
        conn = Conn(box)
        if not throttled.is_set():
            throttled.set()
            conn.fail_chunks_with = IMAPClientError("[THROTTLED] System busy")
        return conn

    pool, _ = _pool(box, connect=connect)
    result = imap_service._ingest_uids(box, acc, "INBOX", "inbox", box.uids, pool=pool)
    pool.close()
    assert result.interrupted
    # 一封不丢：入库的 + 留到下次的 = 全部
    assert result.saved + len(result.failed_uids) == 60
    assert result.saved == _count(acc)
    # 限流不计失败次数
    assert not imap_service._uid_failures


def test_bad_message_only_fails_itself(no_notify):
    acc = create_account()
    box = FakeMailbox(30)

    class BadBox(FakeMailbox):
        def fetch(self, uids, items, modifiers=None):
            if "RFC822" in items and 17 in uids:
                raise IMAPClientError("Some messages could not be FETCHed (Failure)")
            return super().fetch(uids, items, modifiers)

    bad = BadBox(30)

    def connect():
        conn = Conn(box)
        conn.bad_uids = {17}
        return conn

    pool, _ = _pool(box, connect=connect)
    result = imap_service._ingest_uids(bad, acc, "INBOX", "inbox", bad.uids, pool=pool)
    pool.close()
    assert result.failed_uids == [17] and not result.interrupted
    assert result.saved == 29 == _count(acc)


def test_connections_unavailable_falls_back_to_main(no_notify):
    acc = create_account()
    box = FakeMailbox(50)

    def connect():
        raise IMAPClientError("[ALERT] Too many simultaneous connections")

    pool, _ = _pool(box, connect=connect)
    result = imap_service._ingest_uids(box, acc, "INBOX", "inbox", box.uids, pool=pool)
    assert result.saved == 50 and result.connections == 1 and not result.failed_uids


def test_pool_unavailable_mid_way_hands_back_remaining():
    """直接测连接池：所有连接断开后把没下载的批交还调用方。"""
    box = FakeMailbox(40)
    conn = Conn(box)
    conn.fail_chunks_with = IMAPClientError("socket error: EOF")
    pool = FetchPool(lambda: conn, size=1)
    batches = [[1, 2], [3, 4], [5, 6]]
    seen = []
    with pytest.raises(PoolUnavailable) as info:
        for chunk, payload in pool.fetch_batches("INBOX", batches, ["RFC822"]):
            seen.append((chunk, payload))
    assert len(seen) == 1 and isinstance(seen[0][1], IMAPClientError)
    assert info.value.remaining == [[3, 4], [5, 6]]
    assert conn.closed


def test_break_stops_workers_and_close_releases():
    box = FakeMailbox(100)
    pool, conns = _pool(box, size=2)
    batches = [list(range(i, i + 10)) for i in range(1, 101, 10)]
    for chunk, payload in pool.fetch_batches("INBOX", batches, ["RFC822"]):
        assert isinstance(payload, dict) and set(payload) == set(chunk)
        break
    pool.close()
    assert len(conns) == 2 and all(c.closed for c in conns)
