"""服务器 → 本地的反向同步：已读状态（CONDSTORE）、删除 / 归档，以及 UIDVALIDITY 变化。"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from app.database import session_scope
from app.models import Message, PendingOp, SyncState
from app.services import imap_service, outbox
from app.services.imap_service import _sync_one_folder
from tests.helpers import create_account
from tests.test_backfill import FakeMailbox, _count, _state, no_notify  # noqa: F401


def _read_map(account_id: int) -> dict[int, bool]:
    with session_scope() as db:
        rows = db.execute(
            select(Message.uid, Message.is_read).where(Message.account_id == account_id)
        ).all()
    return {int(uid): bool(is_read) for uid, is_read in rows}


def _uids(account_id: int) -> set[int]:
    return set(_read_map(account_id))


def _expire_reconcile(account_id: int) -> None:
    """让下次同步一定执行删除比对（跳过节流）。"""
    with session_scope() as db:
        for state in db.execute(select(SyncState).where(SyncState.account_id == account_id)).scalars():
            if state.reconciled_at:
                state.reconciled_at -= timedelta(hours=1)


def test_read_state_from_other_client_is_synced_with_condstore(no_notify):
    acc = create_account()
    box = FakeMailbox(5)
    box.modseq = 100
    _sync_one_folder(box, acc, "INBOX", "inbox")
    assert _state(acc).highestmodseq == 100
    assert not any(_read_map(acc).values())

    box.set_seen(2)          # 在手机上读了 2
    box.set_seen(4)
    _sync_one_folder(box, acc, "INBOX", "inbox")
    assert _read_map(acc) == {1: False, 2: True, 3: False, 4: True, 5: False}
    assert _state(acc).highestmodseq == box.modseq

    box.set_seen(2, False)   # 又在手机上标为未读
    _sync_one_folder(box, acc, "INBOX", "inbox")
    assert _read_map(acc)[2] is False


def test_pending_local_flag_change_is_not_overwritten(no_notify):
    acc = create_account()
    box = FakeMailbox(3)
    box.modseq = 10
    _sync_one_folder(box, acc, "INBOX", "inbox")

    # 本地刚标为已读，远程操作还在 outbox 排队；服务器上 3 的状态恰好也变了（仍是未读）
    with session_scope() as db:
        msg = db.execute(select(Message).where(Message.account_id == acc, Message.uid == 3)).scalar_one()
        msg.is_read = True
        outbox.enqueue(db, acc, "INBOX", outbox.OP_ADD_FLAGS, [3], "\\Seen")
    box.set_seen(3, False)
    _sync_one_folder(box, acc, "INBOX", "inbox")
    assert _read_map(acc)[3] is True


def test_messages_deleted_elsewhere_are_removed_locally(no_notify):
    acc = create_account()
    box = FakeMailbox(6)
    _sync_one_folder(box, acc, "INBOX", "inbox")
    assert _uids(acc) == {1, 2, 3, 4, 5, 6}

    box.uids.remove(2)       # 在 Gmail 中删除
    box.uids.remove(5)       # 在 Gmail 中归档
    _expire_reconcile(acc)
    _sync_one_folder(box, acc, "INBOX", "inbox")
    assert _uids(acc) == {1, 3, 4, 6}


def test_reconcile_is_throttled(no_notify):
    acc = create_account()
    box = FakeMailbox(3)
    _sync_one_folder(box, acc, "INBOX", "inbox")
    box.uids.remove(1)
    _sync_one_folder(box, acc, "INBOX", "inbox")   # 刚比对过，不会每次同步都 SEARCH ALL
    assert 1 in _uids(acc)
    _expire_reconcile(acc)
    _sync_one_folder(box, acc, "INBOX", "inbox")
    assert 1 not in _uids(acc)


def test_empty_search_on_nonempty_folder_does_not_wipe(no_notify, monkeypatch):
    acc = create_account()
    box = FakeMailbox(4)
    _sync_one_folder(box, acc, "INBOX", "inbox")
    _expire_reconcile(acc)
    original = box.search
    box.search = lambda criteria: [] if criteria == ["ALL"] else original(criteria)
    _sync_one_folder(box, acc, "INBOX", "inbox")
    assert _uids(acc) == {1, 2, 3, 4}


def test_emptied_inbox_is_cleared_locally(no_notify):
    acc = create_account()
    box = FakeMailbox(3)
    _sync_one_folder(box, acc, "INBOX", "inbox")
    box.uids.clear()         # 收件箱清空（inbox zero）
    _expire_reconcile(acc)
    _sync_one_folder(box, acc, "INBOX", "inbox")
    assert _uids(acc) == set()


def test_locally_deleted_mail_is_not_redownloaded_while_remote_delete_pending(no_notify):
    acc = create_account()
    box = FakeMailbox(3)
    _sync_one_folder(box, acc, "INBOX", "inbox")
    # 本地删除了 3，远程删除还没执行；同步状态被重置（例如回填 / 首次同步重跑）
    with session_scope() as db:
        db.delete(db.execute(select(Message).where(Message.account_id == acc, Message.uid == 3)).scalar_one())
        outbox.enqueue(db, acc, "INBOX", outbox.OP_DELETE, [3])
        db.execute(select(SyncState).where(SyncState.account_id == acc)).scalar_one().last_uid = 0
    _sync_one_folder(box, acc, "INBOX", "inbox")
    assert _uids(acc) == {1, 2}


def test_uidvalidity_change_clears_folder_and_resyncs(no_notify):
    acc = create_account()
    box = FakeMailbox(5, uidvalidity=7)
    _sync_one_folder(box, acc, "INBOX", "inbox")
    with session_scope() as db:
        outbox.enqueue(db, acc, "INBOX", outbox.OP_DELETE, [4])

    # 服务器重建了 UID：新体系只有 1..3，且号码比旧的 last_uid 小
    new_box = FakeMailbox(3, uidvalidity=99)
    _sync_one_folder(new_box, acc, "INBOX", "inbox")

    state = _state(acc)
    assert state.uidvalidity == 99
    assert state.last_uid == 3          # 不能再取旧的 max(5, 3)
    assert state.backfill_before_uid is None and not state.backfill_done
    assert _uids(acc) == {1, 2, 3}       # 旧行已清空，新邮件全部入库
    assert _count(acc) == 3
    with session_scope() as db:          # 旧 UID 的远程操作已丢弃
        assert db.execute(select(PendingOp).where(PendingOp.account_id == acc)).first() is None


def test_idle_reacts_to_expunge_and_flag_pushes():
    from app.services.idle_service import _has_new_mail

    assert _has_new_mail([(3, b"EXPUNGE")])
    assert _has_new_mail([(2, b"FETCH", (b"FLAGS", (b"\\Seen",)))])
    assert not _has_new_mail([(b"OK", b"Still here")])
