"""同步引擎的健壮性：限流不丢邮件、大文件夹标签查询分块、回填分小批持锁、空文件夹回填结束。"""
from __future__ import annotations

import threading

import pytest
from imapclient.exceptions import IMAPClientAbortError, IMAPClientError
from sqlalchemy import select

from app.database import session_scope
from app.models import Message, SyncState
from app.services import imap_backfill, imap_service
from app.services import labels as gmail_labels
from app.services.email_parser import _message_base_dir
from app.services.imap_client import IMAPSyncError
from app.services.imap_service import _sync_one_folder
from tests.helpers import create_account
from tests.test_backfill import FakeMailbox, _count, _state, no_notify  # noqa: F401


@pytest.fixture(autouse=True)
def _reset_failures():
    imap_service._uid_failures.clear()
    yield
    imap_service._uid_failures.clear()


class ThrottledMailbox(FakeMailbox):
    """下载正文时按设定抛出错误：限流（整批）或单封邮件取不到。"""

    def __init__(self, n: int):
        super().__init__(n)
        self.throttle_error: Exception | None = None
        self.bad_uids: set[int] = set()
        self.rfc822_calls: list[list[int]] = []

    def fetch(self, uids, items, modifiers=None):
        if "RFC822" in items:
            uids = self._expand(uids)
            self.rfc822_calls.append(list(uids))
            if self.throttle_error is not None:
                raise self.throttle_error
            if self.bad_uids & set(uids):
                raise IMAPClientError("Some messages could not be FETCHed (Failure)")
        return super().fetch(uids, items, modifiers)


@pytest.mark.parametrize("error", [
    IMAPClientError("[OVERQUOTA] Account exceeded command or bandwidth limits. (Failure)"),
    IMAPClientError("[THROTTLED] System busy"),
    IMAPClientAbortError("socket error: EOF"),
])
def test_throttling_never_gives_up_messages(no_notify, monkeypatch, error):
    monkeypatch.setattr(imap_service, "_plan_fetch_batches", lambda client, uids: [uids[i:i + 2] for i in range(0, len(uids), 2)])
    acc = create_account()
    box = ThrottledMailbox(5)
    _sync_one_folder(box, acc, "INBOX", "inbox")

    box.uids.extend([6, 7, 8, 9])
    box.throttle_error = error
    for _ in range(imap_service.MAX_UID_ATTEMPTS + 2):
        box.rfc822_calls.clear()
        _sync_one_folder(box, acc, "INBOX", "inbox")
        assert _state(acc).last_uid == 5            # 一直停在限流的邮件之前，不放弃
        assert len(box.rfc822_calls) == 1           # 第一批被限流后不再继续请求后面的批次
    assert imap_service._uid_failures == {}         # 限流不计失败次数

    box.throttle_error = None
    _sync_one_folder(box, acc, "INBOX", "inbox")
    assert _state(acc).last_uid == 9
    assert _count(acc) == 9


def test_bad_message_in_batch_only_blocks_itself(no_notify):
    acc = create_account()
    box = ThrottledMailbox(5)
    _sync_one_folder(box, acc, "INBOX", "inbox")

    box.uids.extend([6, 7, 8])
    box.bad_uids = {7}
    _sync_one_folder(box, acc, "INBOX", "inbox")
    assert _count(acc) == 7                         # 同批的 6、8 逐封重试后照常入库
    assert _state(acc).last_uid == 6
    assert set(imap_service._uid_failures) == {(acc, "INBOX", 7)}


def test_label_queries_are_chunked(no_notify, monkeypatch):
    monkeypatch.setattr(gmail_labels, "_SQL_CHUNK", 3)
    acc = create_account()
    box = FakeMailbox(10)
    _sync_one_folder(box, acc, "INBOX", "inbox")

    changed = gmail_labels.apply_remote_labels(acc, "INBOX", {u: {"Work"} for u in range(1, 11)}, skip_uids={4})
    assert changed == 9
    with session_scope() as db:
        ids = list(db.execute(select(Message.id).where(Message.account_id == acc).order_by(Message.uid)).scalars())
        labels = gmail_labels.labels_by_message(db, ids)
    assert len(labels) == 9 and all(v[0]["name"] == "Work" for v in labels.values())


def test_label_backfill_fetches_in_ranges(no_notify, monkeypatch):
    monkeypatch.setattr(imap_service, "LABEL_BACKFILL_RANGE", 4)
    acc = create_account()
    box = FakeMailbox(10)
    _sync_one_folder(box, acc, "INBOX", "inbox")
    with session_scope() as db:
        db.execute(SyncState.__table__.update().values(labels_backfilled=False))

    box.thread_ids = {}
    box.labels = {u: ("Work",) for u in range(1, 11)}
    ranges = []
    original = box.fetch

    def spy(uids, items, modifiers=None):
        if items == ["X-GM-LABELS"]:
            ranges.append(uids)
        return original(uids, items, modifiers)

    box.fetch = spy
    assert imap_service._backfill_labels(box, acc, "INBOX") == 10
    assert ranges == ["1:4", "5:8", "9:10"]


def test_backfill_releases_folder_lock_between_chunks(no_notify, monkeypatch):
    monkeypatch.setattr(imap_backfill, "BACKFILL_BATCH_SIZE", 50)
    monkeypatch.setattr(imap_backfill, "IMAP_FETCH_BATCH_SIZE", 10)
    acc = create_account()
    box = FakeMailbox(150)
    _sync_one_folder(box, acc, "INBOX", "inbox")

    lock = threading.Lock()
    held = []
    original = imap_backfill._ingest_uids

    def spy(client, account_id, folder, kind, uids):
        held.append(lock.locked())
        return original(client, account_id, folder, kind, uids)

    monkeypatch.setattr(imap_backfill, "_ingest_uids", spy)
    assert imap_backfill._backfill_batch(box, acc, "INBOX", "inbox", lock) == imap_backfill.DONE
    assert held == [True] * 5        # 每小批都持锁下载……
    assert not lock.locked()         # ……批与批之间、结束后都会释放
    assert _count(acc) == 150


def test_backfill_stops_when_throttled(no_notify, monkeypatch):
    monkeypatch.setattr(imap_backfill, "BACKFILL_BATCH_SIZE", 50)
    acc = create_account()
    box = ThrottledMailbox(160)
    _sync_one_folder(box, acc, "INBOX", "inbox")
    assert imap_backfill._backfill_batch(box, acc, "INBOX", "inbox") == imap_backfill.PROGRESS
    cursor = _state(acc).backfill_before_uid

    box.throttle_error = IMAPClientError("[OVERQUOTA] Account exceeded command or bandwidth limits.")
    with pytest.raises(IMAPSyncError):
        imap_backfill._backfill_batch(box, acc, "INBOX", "inbox")
    assert _state(acc).backfill_before_uid == cursor
    assert imap_service._uid_failures == {}


def test_empty_folder_backfill_finishes(no_notify):
    acc = create_account()
    box = FakeMailbox(0)
    _sync_one_folder(box, acc, "INBOX", "inbox")
    assert imap_backfill._backfill_batch(box, acc, "INBOX", "inbox") == imap_backfill.DONE
    assert not imap_backfill.needs_backfill(acc)

    # 之后一次到达的邮件超过首次同步上限：重新打开回填，把更早的补齐
    box.uids = list(range(1, imap_service.INITIAL_SYNC_LIMIT + 31))
    _sync_one_folder(box, acc, "INBOX", "inbox")
    assert imap_backfill.needs_backfill(acc)
    while imap_backfill._backfill_batch(box, acc, "INBOX", "inbox") == imap_backfill.PROGRESS:
        pass
    assert _count(acc) == imap_service.INITIAL_SYNC_LIMIT + 30


def test_uidvalidity_reset_removes_old_dirs_before_redownload(no_notify):
    acc = create_account()
    box = FakeMailbox(3)
    _sync_one_folder(box, acc, "INBOX", "inbox")
    old_dir = _message_base_dir(acc, "inbox", 2)
    old_dir.mkdir(parents=True, exist_ok=True)
    (old_dir / "stale.bin").write_bytes(b"old")

    imap_service._reset_folder(acc, "INBOX", 99)
    assert not old_dir.exists()      # 当场删除，不留给后台（no_notify 让后台任务不执行）
    assert _count(acc) == 0
