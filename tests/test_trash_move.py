"""删除时本地立即移入「已删除」：列表立刻可见、服务器回收站同步下来时认领同一行（不重复）、
排队中的删除可撤回、认领前的永久删除在认领后执行、收件箱的删除比对不误删、超时未认领的清理。"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from app.database import session_scope
from app.models import Message, PendingOp
from app.services import imap_service, outbox
from app.services.imap_service import _sync_one_folder
from tests.helpers import api_post, create_account, login
from tests.test_backfill import FakeMailbox, _count, no_notify  # noqa: F401


def _raw_with_id(uid: int) -> bytes:
    """FakeMailbox 的邮件没有 Message-ID；认领靠它，这里补上。"""
    from tests.test_backfill import _raw

    return f"Message-ID: <m{uid}@example.com>\r\n".encode() + _raw(uid)


class IdBox(FakeMailbox):
    def fetch(self, uids, items, modifiers=None):
        result = super().fetch(uids, items, modifiers)
        for uid, data in result.items():
            if b"RFC822" in data:
                data[b"RFC822"] = _raw_with_id(uid)
        return result


class TrashBox(IdBox):
    """回收站：邮件内容与收件箱同一封（同样的 Message-ID），只是 UID 不同。"""

    def __init__(self, inbox: FakeMailbox, moved: dict[int, int]):
        super().__init__(0, uidvalidity=9)
        self.inbox = inbox
        self.moved = moved          # 回收站 uid -> 收件箱 uid
        self.uids = sorted(moved)

    def fetch(self, uids, items, modifiers=None):
        result = FakeMailbox.fetch(self, uids, items, modifiers)
        for uid, data in result.items():
            if b"RFC822" in data:
                data[b"RFC822"] = _raw_with_id(self.moved[uid])
        return result


def _ids(acc, kind):
    with session_scope() as db:
        return [int(m.uid) for m in db.execute(select(Message).where(Message.account_id == acc, Message.folder_kind == kind).order_by(Message.uid)).scalars()]


def _delete(client, token, acc, uid):
    with session_scope() as db:
        mid = db.execute(select(Message.id).where(Message.account_id == acc, Message.uid == uid)).scalar_one()
    resp = api_post(client, "/api/messages/actions", token, {"action": "delete", "ids": [int(mid)]})
    assert resp.status_code == 200, resp.text
    return int(mid)


def test_delete_shows_in_trash_immediately_and_is_adopted(client, no_notify, monkeypatch):
    token = login(client)
    acc = create_account()
    inbox = IdBox(5)
    _sync_one_folder(inbox, acc, "INBOX", "inbox")
    monkeypatch.setattr(outbox, "kick", lambda ids: None)   # 不真的去 IMAP 执行

    mid = _delete(client, token, acc, 3)
    # 本地立刻在「已删除」里，收件箱里没有了
    assert _ids(acc, "trash") == [3] and _ids(acc, "inbox") == [1, 2, 4, 5]
    trash_list = client.get("/api/messages", params={"kind": "trash", "account_id": acc}).json()
    assert [m["id"] for m in trash_list["items"]] == [mid]
    with session_scope() as db:
        row = db.get(Message, mid)
        assert row.moved_at is not None and row.moved_from_kind == "inbox" and row.folder == "INBOX"
        ops = db.execute(select(PendingOp).where(PendingOp.account_id == acc)).scalars().all()
        assert [(o.folder, o.op, o.uids) for o in ops] == [("INBOX", "delete", "3")]

    # 收件箱的删除比对：服务器上已没有 uid 3，但本地这行是刚移走的，不能被当成「服务器删了」清掉
    inbox.uids.remove(3)
    _sync_one_folder(inbox, acc, "INBOX", "inbox")
    assert _ids(acc, "trash") == [3]

    # 服务器回收站里出现了它（新 uid 101）：认领同一行，不新建
    trash = TrashBox(inbox, {101: 3})
    trash.seen.add(101)
    _sync_one_folder(trash, acc, "[Gmail]/Trash", "trash")
    with session_scope() as db:
        rows = db.execute(select(Message).where(Message.account_id == acc, Message.folder_kind == "trash")).scalars().all()
        assert len(rows) == 1 and rows[0].id == mid
        assert rows[0].folder == "[Gmail]/Trash" and rows[0].uid == 101 and rows[0].moved_at is None
        assert rows[0].is_read is True and rows[0].body_text == "body 3"
    assert _count(acc) == 5


def test_restore_cancels_pending_delete(client, no_notify, monkeypatch):
    token = login(client)
    acc = create_account()
    inbox = IdBox(3)
    _sync_one_folder(inbox, acc, "INBOX", "inbox")
    monkeypatch.setattr(outbox, "kick", lambda ids: None)
    mid = _delete(client, token, acc, 2)
    resp = api_post(client, "/api/messages/actions", token, {"action": "restore", "ids": [mid]})
    assert resp.status_code == 200
    assert _ids(acc, "inbox") == [1, 2, 3] and _ids(acc, "trash") == []
    with session_scope() as db:
        assert db.execute(select(PendingOp).where(PendingOp.account_id == acc)).scalars().all() == []
        assert db.get(Message, mid).moved_at is None


def test_purge_before_adoption_deletes_on_server_after(client, no_notify, monkeypatch):
    token = login(client)
    acc = create_account()
    inbox = IdBox(3)
    _sync_one_folder(inbox, acc, "INBOX", "inbox")
    monkeypatch.setattr(outbox, "kick", lambda ids: None)
    mid = _delete(client, token, acc, 1)
    # 认领前再点永久删除：先从列表里隐藏
    api_post(client, "/api/messages/actions", token, {"action": "delete", "ids": [mid]})
    assert client.get("/api/messages", params={"kind": "trash", "account_id": acc}).json()["items"] == []
    with session_scope() as db:
        assert db.get(Message, mid).purge_pending is True
    # 回收站同步到它：本地行删除，服务器上彻底删除的操作入队
    trash = TrashBox(inbox, {201: 1})
    _sync_one_folder(trash, acc, "[Gmail]/Trash", "trash")
    assert _ids(acc, "trash") == []
    with session_scope() as db:
        ops = db.execute(select(PendingOp).where(PendingOp.account_id == acc, PendingOp.folder == "[Gmail]/Trash")).scalars().all()
        assert [(o.op, o.uids) for o in ops] == [("delete", "201")]


def test_unadopted_move_is_cleaned_up(no_notify, monkeypatch):
    acc = create_account()
    inbox = IdBox(2)
    _sync_one_folder(inbox, acc, "INBOX", "inbox")
    with session_scope() as db:
        row = db.execute(select(Message).where(Message.account_id == acc, Message.uid == 2)).scalar_one()
        row.folder_kind = "trash"
        row.moved_from_kind = "inbox"
        row.moved_at = datetime.now() - timedelta(hours=1)
    trash = TrashBox(inbox, {})
    _sync_one_folder(trash, acc, "[Gmail]/Trash", "trash")
    assert _ids(acc, "trash") == [] and _count(acc) == 1
