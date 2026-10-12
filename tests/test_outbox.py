"""远程操作 outbox：与本地修改同一事务入队，失败按退避重试，UID 失效时丢弃。"""
from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.database import session_scope
from app.models import Message, PendingOp, SyncState
from app.services import outbox
from app.timeutil import utcnow
from tests.helpers import api_post, create_account, create_message, login


def _ops(account_id: int) -> list[PendingOp]:
    with session_scope() as db:
        rows = db.execute(
            select(PendingOp).where(PendingOp.account_id == account_id).order_by(PendingOp.id)
        ).scalars().all()
        for row in rows:
            db.expunge(row)
        return rows


def _set_validity(account_id: int, folder: str, value: int) -> None:
    with session_scope() as db:
        state = db.execute(
            select(SyncState).where(SyncState.account_id == account_id, SyncState.folder == folder)
        ).scalar_one_or_none()
        if state is None:
            db.add(SyncState(account_id=account_id, folder=folder, last_uid=0, uidvalidity=value))
        else:
            state.uidvalidity = value


def test_batch_delete_enqueues_remote_op_with_local_delete(client, _no_background_network):
    token = login(client)
    acc = create_account()
    _set_validity(acc, "INBOX", 7)
    m1 = create_message(acc, uid=501)
    m2 = create_message(acc, uid=502)
    resp = api_post(client, "/api/messages/actions", token, {"action": "delete", "ids": [m1, m2]})
    assert resp.status_code == 200
    with session_scope() as db:
        assert db.get(Message, m1) is None and db.get(Message, m2) is None
    ops = _ops(acc)
    assert [(o.op, o.folder, o.uids, o.uidvalidity) for o in ops] == [("delete", "INBOX", "501,502", 7)]
    assert ("process_account", (acc,), {}) in _no_background_network


def test_mark_read_enqueues_flag_op(client):
    token = login(client)
    acc = create_account()
    mid = create_message(acc, uid=601, is_read=False)
    assert api_post(client, "/api/messages/actions", token, {"action": "read", "ids": [mid]}).status_code == 200
    ops = _ops(acc)
    assert [(o.op, o.uids, o.arg) for o in ops] == [("add_flags", "601", "\\Seen")]


def test_success_removes_op(monkeypatch):
    acc = create_account()
    with session_scope() as db:
        outbox.enqueue(db, acc, "INBOX", outbox.OP_DELETE, [3, 1])
    calls = []
    monkeypatch.setattr(
        outbox, "delete_imap_messages",
        lambda account_id, pairs: calls.append(pairs) or {"ok": True, "folders": {"INBOX": {"error": None}}},
    )
    outbox.process_account(acc)
    assert calls == [[("INBOX", [1, 3])]]
    assert _ops(acc) == []


def test_failure_backs_off_and_retries_later(monkeypatch):
    acc = create_account()
    with session_scope() as db:
        outbox.enqueue(db, acc, "INBOX", outbox.OP_DELETE, [5])
    monkeypatch.setattr(outbox, "delete_imap_messages", lambda *a, **k: {"ok": False, "error": "busy", "folders": {}})
    outbox.process_account(acc)
    (op,) = _ops(acc)
    assert op.attempts == 1 and op.last_error == "busy"
    assert op.next_attempt_at > utcnow() + timedelta(seconds=20)

    # 还没到重试时间：不会再执行
    called = []
    monkeypatch.setattr(outbox, "delete_imap_messages", lambda *a, **k: called.append(1) or {"ok": True})
    outbox.process_account(acc)
    assert called == []

    # 到期后重试成功，任务删除
    with session_scope() as db:
        db.get(PendingOp, op.id).next_attempt_at = utcnow() - timedelta(seconds=1)
    outbox.process_account(acc)
    assert called == [1] and _ops(acc) == []


def test_backoff_is_capped(monkeypatch):
    acc = create_account()
    with session_scope() as db:
        outbox.enqueue(db, acc, "INBOX", outbox.OP_DELETE, [5])
    monkeypatch.setattr(outbox, "delete_imap_messages", lambda *a, **k: {"ok": False, "error": "x"})
    for _ in range(15):
        with session_scope() as db:
            db.execute(select(PendingOp).where(PendingOp.account_id == acc)).scalar_one().next_attempt_at = utcnow()
        outbox.process_account(acc)
    (op,) = _ops(acc)
    assert op.attempts == 15
    assert op.next_attempt_at <= utcnow() + timedelta(seconds=outbox.MAX_BACKOFF_SEC + 5)


def test_stale_uidvalidity_op_is_dropped(monkeypatch):
    acc = create_account()
    _set_validity(acc, "INBOX", 7)
    with session_scope() as db:
        outbox.enqueue(db, acc, "INBOX", outbox.OP_DELETE, [9])
    _set_validity(acc, "INBOX", 8)  # 服务器 UID 体系变了
    monkeypatch.setattr(outbox, "delete_imap_messages", lambda *a, **k: pytest.fail("must not run"))
    outbox.process_account(acc)
    assert _ops(acc) == []


def test_flag_and_move_ops_call_imap(monkeypatch):
    acc = create_account()
    with session_scope() as db:
        outbox.enqueue(db, acc, "INBOX", outbox.OP_REMOVE_FLAGS, [4], "\\Seen")
        outbox.enqueue(db, acc, "[Gmail]/Spam", outbox.OP_MOVE, [8], "INBOX")
    seen = []
    monkeypatch.setattr(outbox, "update_imap_flags", lambda a, pairs, **k: seen.append((pairs, k)) or {"ok": True})
    monkeypatch.setattr(outbox, "move_imap_messages", lambda a, f, u, target: seen.append((f, u, target)) or {"ok": True})
    outbox.process_account(acc)
    assert seen == [([("INBOX", [4])], {"remove_flags": [b"\\Seen"]}), ("[Gmail]/Spam", [8], "INBOX")]
    assert _ops(acc) == []


def test_process_all_due_kicks_accounts(_no_background_network):
    acc = create_account()
    with session_scope() as db:
        outbox.enqueue(db, acc, "INBOX", outbox.OP_DELETE, [1])
    outbox.process_all_due()
    assert ("process_account", (acc,), {}) in _no_background_network


def test_newer_flag_op_supersedes_failed_older_one(monkeypatch):
    """先标已读失败、再标未读成功：已读的重试不能把服务器改回已读。"""
    acc = create_account()
    calls = []

    def flags(account_id, pairs, add_flags=None, remove_flags=None):
        calls.append(("add" if add_flags else "remove", pairs[0][1]))
        return {"ok": calls[-1][0] == "remove", "error": "busy", "folders": {}}

    monkeypatch.setattr(outbox, "update_imap_flags", flags)
    with session_scope() as db:
        outbox.enqueue(db, acc, "INBOX", outbox.OP_ADD_FLAGS, [1, 2], "\Seen")
    outbox.process_account(acc)                      # 标已读失败，退避
    with session_scope() as db:
        outbox.enqueue(db, acc, "INBOX", outbox.OP_REMOVE_FLAGS, [2], "\Seen")
        outbox.enqueue(db, acc, "INBOX", outbox.OP_ADD_FLAGS, [2], "\Flagged")  # 别的标记不受影响
    outbox.process_account(acc)

    ops = _ops(acc)
    remaining = {(op.op, op.arg): op.uids for op in ops}
    assert remaining[(outbox.OP_ADD_FLAGS, "\Seen")] == "1"    # 只剩 1 还要标已读
    assert (outbox.OP_REMOVE_FLAGS, "\Seen") not in remaining
    assert remaining[(outbox.OP_ADD_FLAGS, "\Flagged")] == "2"


def test_fully_superseded_op_is_removed():
    acc = create_account()
    with session_scope() as db:
        outbox.enqueue(db, acc, "INBOX", outbox.OP_ADD_LABEL, [3], "Work")
    with session_scope() as db:
        outbox.enqueue(db, acc, "INBOX", outbox.OP_REMOVE_LABEL, [3], "Work")
        outbox.enqueue(db, acc, "INBOX", outbox.OP_DELETE, [3])
    assert [(op.op, op.uids) for op in _ops(acc)] == [
        (outbox.OP_REMOVE_LABEL, "3"), (outbox.OP_DELETE, "3"),
    ]
