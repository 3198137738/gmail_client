"""历史邮件回填：首次同步只取最新一批，之后在后台把更早的邮件全部补齐。"""
from __future__ import annotations

from contextlib import contextmanager
from email.message import EmailMessage

import pytest
from imapclient.exceptions import IMAPClientError
from sqlalchemy import func, select

from app.database import session_scope
from app.models import Message, SyncState
from app.services import imap_backfill, imap_service
from app.services.imap_service import _sync_one_folder
from tests.helpers import create_account


def _raw(uid: int) -> bytes:
    msg = EmailMessage()
    msg["From"] = f"Sender {uid} <s{uid}@example.com>"
    msg["To"] = "me@example.com"
    msg["Subject"] = f"mail {uid}"
    msg.set_content(f"body {uid}")
    return msg.as_bytes()


class FakeMailbox:
    """模拟一个 Gmail 文件夹：UID 1..n。"""

    def __init__(self, n: int, uidvalidity: int = 7):
        self.uids = list(range(1, n + 1))
        self.uidvalidity = uidvalidity
        self.fail_fetch = False
        self.seen: set[int] = set()          # 服务器上已读的 UID
        self.modseq: int | None = None       # None 表示不支持 CONDSTORE
        self.changed_since: dict[int, int] = {}  # uid -> 最后一次变化时的 modseq
        self.thread_ids: dict[int, int] | None = None  # 设置后模拟 Gmail 扩展（X-GM-THRID）

    def select_folder(self, folder, readonly=True):
        info = {b"UIDVALIDITY": self.uidvalidity, b"EXISTS": len(self.uids)}
        if self.modseq is not None:
            info[b"HIGHESTMODSEQ"] = self.modseq
        return info

    def has_capability(self, cap):
        if cap == "X-GM-EXT-1":
            return self.thread_ids is not None
        return cap == "CONDSTORE" and self.modseq is not None

    def set_seen(self, uid: int, seen: bool = True):
        """模拟在其他客户端修改已读状态。"""
        (self.seen.add if seen else self.seen.discard)(uid)
        if self.modseq is not None:
            self.modseq += 1
            self.changed_since[uid] = self.modseq

    def _expand(self, uids):
        if isinstance(uids, str):
            lo, hi = uids.split(":")
            hi = max(self.uids, default=0) if hi == "*" else int(hi)
            return [u for u in self.uids if int(lo) <= u <= hi]
        return list(uids)

    def search(self, criteria):
        if criteria == ["ALL"]:
            return list(self.uids)
        assert criteria[0] == "UID"
        lo, hi = criteria[1].split(":")
        lo = int(lo)
        hi = max(self.uids, default=0) if hi == "*" else int(hi)
        return [u for u in self.uids if lo <= u <= hi]

    def _flags(self, uid):
        return (b"\\Seen",) if uid in self.seen else ()

    def fetch(self, uids, items, modifiers=None):
        uids = self._expand(uids)
        if items == ["RFC822.SIZE"]:
            return {u: {b"RFC822.SIZE": 1000} for u in uids}
        if items == ["X-GM-THRID"]:
            return {u: {b"X-GM-THRID": self.thread_ids.get(u)} for u in uids if u in self.uids}
        if items == ["FLAGS"]:
            if modifiers:
                since = int(modifiers[0].split()[1])
                uids = [u for u in uids if self.changed_since.get(u, 0) > since]
            return {u: {b"FLAGS": self._flags(u)} for u in uids if u in self.uids}
        if self.fail_fetch:
            raise IMAPClientError("connection reset")
        result = {
            u: {b"RFC822": _raw(u), b"FLAGS": self._flags(u), b"INTERNALDATE": None}
            for u in uids if u in self.uids
        }
        if "X-GM-THRID" in items:
            for u, data in result.items():
                data[b"X-GM-THRID"] = self.thread_ids.get(u)
        return result

    def list_folders(self):
        return []


@pytest.fixture()
def no_notify(monkeypatch):
    """回填不能触发 Telegram / 新邮件提示。"""
    calls = []
    monkeypatch.setattr(imap_service, "submit_bg", lambda fn, *a, **k: calls.append(fn))
    monkeypatch.setattr(imap_service, "publish_new_mail", lambda *a, **k: calls.append("sse"))
    return calls


def _count(account_id: int) -> int:
    with session_scope() as db:
        return db.execute(
            select(func.count(Message.id)).where(Message.account_id == account_id)
        ).scalar()


def _state(account_id: int) -> SyncState:
    with session_scope() as db:
        state = db.execute(
            select(SyncState).where(SyncState.account_id == account_id)
        ).scalar_one()
        db.expunge(state)
        return state


def test_initial_sync_then_backfill_everything(no_notify, monkeypatch):
    monkeypatch.setattr(imap_backfill, "BACKFILL_BATCH_SIZE", 120)
    acc = create_account()
    box = FakeMailbox(450)

    _sync_one_folder(box, acc, "INBOX", "inbox")
    assert _count(acc) == 100  # 首次同步只取最新 100 封

    statuses = []
    while True:
        status = imap_backfill._backfill_batch(box, acc, "INBOX", "inbox")
        statuses.append(status)
        if status != imap_backfill.PROGRESS:
            break
    assert statuses == ["progress", "progress", "done"]  # 350 封分 120/120/110 三批
    assert _count(acc) == 450
    assert _state(acc).backfill_done
    assert no_notify == []

    # 之后再收到的新邮件仍由增量同步处理
    box.uids.append(451)
    _sync_one_folder(box, acc, "INBOX", "inbox")
    assert _count(acc) == 451


def test_backfill_resumes_after_failure(no_notify, monkeypatch):
    monkeypatch.setattr(imap_backfill, "BACKFILL_BATCH_SIZE", 50)
    acc = create_account()
    box = FakeMailbox(160)
    _sync_one_folder(box, acc, "INBOX", "inbox")
    assert imap_backfill._backfill_batch(box, acc, "INBOX", "inbox") == imap_backfill.PROGRESS
    cursor = _state(acc).backfill_before_uid

    box.fail_fetch = True
    assert imap_backfill._backfill_batch(box, acc, "INBOX", "inbox") == imap_backfill.FAILED
    assert _state(acc).backfill_before_uid == cursor  # 出错不推进断点

    box.fail_fetch = False
    while imap_backfill._backfill_batch(box, acc, "INBOX", "inbox") == imap_backfill.PROGRESS:
        pass
    assert _count(acc) == 160


def test_backfill_waits_for_initial_sync(no_notify):
    acc = create_account()
    with session_scope() as db:
        db.add(SyncState(account_id=acc, folder="INBOX", last_uid=0))
    assert imap_backfill._backfill_batch(FakeMailbox(10), acc, "INBOX", "inbox") == imap_backfill.SKIP


def test_uidvalidity_change_restarts_backfill(no_notify):
    acc = create_account()
    box = FakeMailbox(120)
    _sync_one_folder(box, acc, "INBOX", "inbox")
    while imap_backfill._backfill_batch(box, acc, "INBOX", "inbox") == imap_backfill.PROGRESS:
        pass
    assert _state(acc).backfill_done

    box.uidvalidity = 8
    _sync_one_folder(box, acc, "INBOX", "inbox")
    state = _state(acc)
    assert not state.backfill_done and state.backfill_before_uid is None


def test_run_backfill_end_to_end(no_notify, monkeypatch):
    monkeypatch.setattr(imap_backfill, "BACKFILL_BATCH_SIZE", 100)
    monkeypatch.setattr(imap_backfill, "BACKFILL_PAUSE_SEC", 0)
    acc = create_account()
    box = FakeMailbox(330)
    _sync_one_folder(box, acc, "INBOX", "inbox")

    @contextmanager
    def fake_client(email, password, timeout=None):
        yield box

    monkeypatch.setattr(imap_backfill, "_imap_client", fake_client)
    monkeypatch.setattr(imap_backfill, "sync_targets", lambda client, aid: [("INBOX", "inbox")])

    imap_backfill.start_backfill()  # 模拟应用运行中（其他测试关闭应用时会设置停止标志）
    imap_backfill._running.add(acc)
    imap_backfill._run_backfill(acc)
    assert _count(acc) == 330
    assert not imap_backfill.is_backfilling(acc)
    assert not imap_backfill.needs_backfill(acc)
