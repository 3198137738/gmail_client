"""同步中途失败时不能推进 last_uid，否则失败的邮件会永久丢失。"""
from __future__ import annotations

import pytest
from imapclient.exceptions import IMAPClientError

from app.services import imap_service
from app.services.imap_service import _sync_one_folder
from tests.helpers import create_account
from tests.test_backfill import FakeMailbox, _count, _raw, _state, no_notify  # noqa: F401


@pytest.fixture(autouse=True)
def _reset_failures():
    imap_service._uid_failures.clear()
    imap_service._uid_transient.clear()
    yield
    imap_service._uid_failures.clear()
    imap_service._uid_transient.clear()


class FlakyMailbox(FakeMailbox):
    """指定 UID 下载失败（网络错误）或内容损坏（解析失败）。"""

    def __init__(self, n: int):
        super().__init__(n)
        self.fail_uids: set[int] = set()
        self.broken_uids: set[int] = set()

    def fetch(self, uids, items, modifiers=None):
        if items in (["RFC822.SIZE"], ["FLAGS"]):
            return super().fetch(uids, items, modifiers)
        if self.fail_uids & set(uids):
            raise IMAPClientError("socket timeout")
        return {
            u: {b"RFC822": b"" if u in self.broken_uids else _raw(u), b"FLAGS": (), b"INTERNALDATE": None}
            for u in uids
        }


def test_fetch_failure_holds_last_uid_and_retries(no_notify, monkeypatch):
    # 每封邮件单独成批，只有 UID 12 那批失败
    monkeypatch.setattr(imap_service, "_plan_fetch_batches", lambda client, uids: [[u] for u in uids])
    acc = create_account()
    box = FlakyMailbox(10)
    _sync_one_folder(box, acc, "INBOX", "inbox")
    assert _state(acc).last_uid == 10

    box.uids.extend([11, 12, 13])
    box.fail_uids = {12}
    _sync_one_folder(box, acc, "INBOX", "inbox")
    assert _state(acc).last_uid == 11   # 停在失败的 12 之前
    assert _count(acc) == 12            # 11 和 13 已入库

    box.fail_uids = set()
    _sync_one_folder(box, acc, "INBOX", "inbox")
    assert _state(acc).last_uid == 13
    assert _count(acc) == 13


def test_broken_message_is_given_up_after_max_attempts(no_notify):
    acc = create_account()
    box = FlakyMailbox(5)
    _sync_one_folder(box, acc, "INBOX", "inbox")

    box.uids.extend([6, 7])
    box.broken_uids = {6}
    for _ in range(imap_service.MAX_UID_ATTEMPTS - 1):
        _sync_one_folder(box, acc, "INBOX", "inbox")
        assert _state(acc).last_uid == 5   # 仍在重试，进度停在 6 之前
        assert _count(acc) == 6            # 后面的新邮件照常入库

    _sync_one_folder(box, acc, "INBOX", "inbox")
    assert _state(acc).last_uid == 7       # 达到上限后放弃，不再阻塞
    assert imap_service._uid_failures == {}
