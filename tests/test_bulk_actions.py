"""批量整理：批量星标、归档、举报垃圾邮件、跨账号批量标签、预先加载不标已读、特殊文件夹移动。"""
from __future__ import annotations

from contextlib import contextmanager

from app.crypto import encrypt_password
from app.database import session_scope
from app.models import Account, Label, Message, MessageLabel, PendingOp
from app.services import imap_ops, outbox
from tests.helpers import api_post, create_account, create_message, login


def _ops(account_id: int, op: str | None = None) -> list[PendingOp]:
    with session_scope() as db:
        q = db.query(PendingOp).filter_by(account_id=account_id)
        if op:
            q = q.filter_by(op=op)
        rows = q.all()
        db.expunge_all()
        return rows


def _outlook_account() -> int:
    acc = create_account()
    with session_scope() as db:
        db.get(Account, acc).encrypted_password = encrypt_password("msoauth:refresh")
    return acc


def test_bulk_star_and_unstar(client):
    token = login(client)
    acc = create_account()
    ids = [create_message(acc), create_message(acc)]
    assert api_post(client, "/api/messages/actions", token, {"action": "star", "ids": ids}).json()["affected"] == 2
    with session_scope() as db:
        assert all(db.get(Message, i).is_starred for i in ids)
    assert [o.op for o in _ops(acc)] == ["add_flags"]
    api_post(client, "/api/messages/actions", token, {"action": "unstar", "ids": ids[:1]})
    with session_scope() as db:
        assert not db.get(Message, ids[0]).is_starred and db.get(Message, ids[1]).is_starred


def test_archive_moves_to_provider_specific_folder(client):
    token = login(client)
    gmail = create_account()
    outlook = _outlook_account()
    g_inbox = create_message(gmail, uid=11)
    g_sent = create_message(gmail, uid=12, folder="[Gmail]/Sent Mail", folder_kind="sent")
    o_inbox = create_message(outlook, uid=21)

    resp = api_post(client, "/api/messages/actions", token, {"action": "archive", "ids": [g_inbox, g_sent, o_inbox]})
    assert resp.json()["affected"] == 2   # 已发送的邮件不归档
    with session_scope() as db:
        assert db.get(Message, g_inbox) is None and db.get(Message, o_inbox) is None
        assert db.get(Message, g_sent) is not None
    (g_op,) = _ops(gmail, "move")
    (o_op,) = _ops(outlook, "move")
    assert (g_op.folder, g_op.uids, g_op.arg) == ("INBOX", "11", "\\All")
    assert (o_op.uids, o_op.arg) == ("21", "\\Archive")
    # 同步时跳过待移动的 UID，邮件不会被重新收回来
    with session_scope() as db:
        assert 11 in outbox.pending_uids(db, gmail, "INBOX", ops=(outbox.OP_DELETE, outbox.OP_MOVE))


def test_report_spam_moves_to_junk(client):
    token = login(client)
    acc = create_account()
    inbox = create_message(acc, uid=31)
    trash = create_message(acc, uid=32, folder="[Gmail]/Trash", folder_kind="trash")
    resp = api_post(client, "/api/messages/actions", token, {"action": "spam", "ids": [inbox, trash]})
    assert resp.json()["affected"] == 1
    (op,) = _ops(acc, "move")
    assert (op.uids, op.arg) == ("31", "\\Junk")
    with session_scope() as db:
        assert db.get(Message, inbox) is None and db.get(Message, trash) is not None


def test_bulk_labels_across_accounts(client):
    token = login(client)
    a, b = create_account(), create_account()
    ma, mb = create_message(a, uid=41), create_message(b, uid=42)
    with session_scope() as db:
        la, lb, old = Label(account_id=a, name="A标签"), Label(account_id=b, name="B标签"), Label(account_id=a, name="旧")
        db.add_all([la, lb, old])
        db.flush()
        db.add(MessageLabel(message_id=ma, label_id=old.id))
        la_id, lb_id, old_id = la.id, lb.id, old.id

    resp = api_post(client, "/api/messages/labels", token, {"ids": [ma, mb], "add": [la_id, lb_id], "remove": [old_id]})
    assert resp.status_code == 200
    with session_scope() as db:
        labels_a = {r.label_id for r in db.query(MessageLabel).filter_by(message_id=ma)}
        labels_b = {r.label_id for r in db.query(MessageLabel).filter_by(message_id=mb)}
    # 每封邮件只加自己账号的标签
    assert labels_a == {la_id} and labels_b == {lb_id}
    assert {(o.op, o.arg) for o in _ops(a)} == {("add_label", "A标签"), ("remove_label", "旧")}
    assert {(o.op, o.arg) for o in _ops(b)} == {("add_label", "B标签")}


def test_peek_does_not_mark_read(client):
    login(client)
    acc = create_account()
    mid = create_message(acc, is_read=False)
    assert client.get(f"/api/messages/{mid}", params={"peek": 1}).status_code == 200
    with session_scope() as db:
        assert db.get(Message, mid).is_read is False
    client.get(f"/api/messages/{mid}")
    with session_scope() as db:
        assert db.get(Message, mid).is_read is True


def test_move_resolves_special_folder(monkeypatch):
    acc = create_account()
    moved = []

    class FakeClient:
        def list_folders(self):
            return [((b"\\HasNoChildren", b"\\All"), b"/", "[Gmail]/All Mail")]

        def select_folder(self, folder, readonly=False):
            pass

        def has_capability(self, cap):
            return True

        def move(self, uids, target):
            moved.append((list(uids), target))

    @contextmanager
    def fake_imap(email, password, **kw):
        yield FakeClient()

    monkeypatch.setattr(imap_ops, "_imap_client", fake_imap)
    assert imap_ops.move_imap_messages(acc, "INBOX", [5], "\\All")["ok"]
    assert moved == [([5], "[Gmail]/All Mail")]
    result = imap_ops.move_imap_messages(acc, "INBOX", [6], "\\Junk")
    assert result == {"ok": False, "moved": 0, "error": "no_special_folder"}
