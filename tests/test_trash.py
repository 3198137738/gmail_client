"""左侧「已删除」：同步 Gmail 的已删除邮件，可移回收件箱、永久删除、清空。"""
from __future__ import annotations

from sqlalchemy import select

from app.database import session_scope
from app.models import Message, PendingOp
from tests.helpers import api_post, create_account, create_message, login

TRASH = "[Gmail]/已删除邮件"


def _ids(client, kind, acc):
    data = client.get("/api/messages", params={"kind": kind, "account_id": acc}).json()
    return {m["id"] for m in data["items"]}, data


def test_trash_is_its_own_folder(client):
    login(client)
    acc = create_account()
    inbox = create_message(acc)
    trashed = create_message(acc, folder=TRASH, folder_kind="trash", is_starred=True)

    ids, data = _ids(client, "trash", acc)
    assert ids == {trashed} and data["kind_label"] == "已删除"
    # 与 Gmail 一致：所有邮件、已加星标里都不包含已删除邮件
    assert _ids(client, "all", acc)[0] == {inbox}
    assert _ids(client, "starred", acc)[0] == set()


def test_trash_not_grouped_with_inbox_conversation(client):
    login(client)
    acc = create_account()
    create_message(acc, thread_id=77)
    trashed = create_message(acc, folder=TRASH, folder_kind="trash", thread_id=77)
    ids, data = _ids(client, "trash", acc)
    assert ids == {trashed}
    assert data["items"][0]["thread_count"] == 1


def test_delete_in_trash_is_permanent_and_restore_moves_to_inbox(client, _no_background_network):
    token = login(client)
    acc = create_account()
    a = create_message(acc, folder=TRASH, folder_kind="trash", uid=501)
    b = create_message(acc, folder=TRASH, folder_kind="trash", uid=502)
    inbox = create_message(acc, uid=503)

    resp = api_post(client, "/api/messages/actions", token, json={"action": "restore", "ids": [a, inbox]})
    assert resp.json()["affected"] == 1   # 不在已删除里的邮件不受影响
    resp = api_post(client, "/api/messages/actions", token, json={"action": "delete", "ids": [b]})
    assert resp.json()["affected"] == 1

    with session_scope() as db:
        assert db.get(Message, a) is None and db.get(Message, b) is None
        assert db.get(Message, inbox) is not None
        ops = {(o.op, o.folder, o.uids, o.arg) for o in db.execute(
            select(PendingOp).where(PendingOp.account_id == acc)).scalars()}
    # 移回收件箱；在已删除邮件文件夹里删除，执行时会直接 EXPUNGE（永久删除）
    assert ops == {("move", TRASH, "501", "INBOX"), ("delete", TRASH, "502", None)}


def test_empty_trash_only_touches_that_account(client, _no_background_network):
    token = login(client)
    acc = create_account()
    other = create_account()
    mine = [create_message(acc, folder=TRASH, folder_kind="trash") for _ in range(2)]
    keep_inbox = create_message(acc)
    keep_other = create_message(other, folder=TRASH, folder_kind="trash")

    resp = api_post(client, "/api/messages/empty-trash", token, json={"account_id": acc})
    assert resp.json() == {"ok": True, "affected": 2}
    with session_scope() as db:
        assert all(db.get(Message, mid) is None for mid in mine)
        assert db.get(Message, keep_inbox) is not None
        assert db.get(Message, keep_other) is not None


def test_sync_targets_include_trash():
    from app.services.imap_service import sync_targets

    class Client:
        def list_folders(self):
            return [
                ((b"\HasNoChildren",), b"/", "INBOX"),
                ((b"\Sent",), b"/", "[Gmail]/已发送邮件"),
                ((b"\Trash",), b"/", TRASH),
            ]

    assert (TRASH, "trash") in sync_targets(Client(), 1)
