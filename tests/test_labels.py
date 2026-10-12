"""Gmail 标签：标签列表同步、邮件标签同步（下载 / 远程变化 / 补齐）、标签视图、修改标签。"""
from __future__ import annotations

from sqlalchemy import select

from app.database import session_scope
from app.models import Label, Message, MessageLabel, PendingOp
from app.services import labels as gmail_labels
from app.services.imap_service import _sync_one_folder
from tests.helpers import api_post, create_account, create_message, login
from tests.test_backfill import FakeMailbox, no_notify  # noqa: F401


def _labels_of(account_id):
    with session_scope() as db:
        rows = db.execute(
            select(Message.uid, Label.name)
            .join(MessageLabel, MessageLabel.message_id == Message.id)
            .join(Label, Label.id == MessageLabel.label_id)
            .where(Message.account_id == account_id)
        ).all()
    result = {}
    for uid, name in rows:
        result.setdefault(int(uid), set()).add(name)
    return result


def test_discover_and_parse_labels():
    class Client:
        def list_folders(self):
            return [
                ((b"\\HasNoChildren",), b"/", "INBOX"),
                ((b"\\Noselect", b"\\HasChildren"), b"/", "[Gmail]"),
                ((b"\\HasNoChildren", b"\\All"), b"/", "[Gmail]/所有邮件"),
                ((b"\\HasNoChildren", b"\\Sent"), b"/", "[Gmail]/已发送邮件"),
                ((b"\\HasNoChildren",), b"/", "工作"),
                ((b"\\HasNoChildren",), b"/", "工作/项目"),
            ]

    assert gmail_labels.discover_label_names(Client()) == ["工作", "工作/项目"]
    # X-GM-LABELS 中的系统标签被忽略，非 ASCII 标签为 modified UTF-7
    assert gmail_labels.parse_labels((b"\\Inbox", b"\\Important", b"&XeVPXA-", b"Travel")) == {"工作", "Travel"}


def test_sync_label_list_adds_and_removes():
    acc = create_account()
    gmail_labels.sync_label_list(acc, ["A", "B"])
    gmail_labels.sync_label_list(acc, ["B", "C"])
    with session_scope() as db:
        assert sorted(db.execute(select(Label.name).where(Label.account_id == acc)).scalars()) == ["B", "C"]


def test_labels_synced_on_download_remote_change_and_backfill(no_notify):
    acc = create_account()
    box = FakeMailbox(3)
    _sync_one_folder(box, acc, "INBOX", "inbox")          # 不支持 Gmail 扩展时没有标签
    assert _labels_of(acc) == {}

    # 开启 Gmail 扩展：已入库邮件一次性补齐标签
    box.thread_ids = {}
    box.modseq = 10
    box.labels = {1: (b"\\Inbox", b"Work"), 2: (b"Travel",)}
    _sync_one_folder(box, acc, "INBOX", "inbox")
    assert _labels_of(acc) == {1: {"Work"}, 2: {"Travel"}}

    # 新邮件下载时带上标签
    box.uids.append(4)
    box.labels[4] = (b"Work", b"Travel")
    _sync_one_folder(box, acc, "INBOX", "inbox")
    assert _labels_of(acc)[4] == {"Work", "Travel"}

    # 在 Gmail 中改了 1 的标签（CONDSTORE 变化）
    box.labels[1] = (b"Travel",)
    box.modseq += 1
    box.changed_since[1] = box.modseq
    _sync_one_folder(box, acc, "INBOX", "inbox")
    assert _labels_of(acc)[1] == {"Travel"}


def test_label_view_lists_labeled_messages(client):
    login(client)
    acc = create_account()
    gmail_labels.sync_label_list(acc, ["项目"])
    with session_scope() as db:
        label_id = db.execute(select(Label.id).where(Label.account_id == acc)).scalar_one()
    tagged = create_message(acc, subject="带标签的邮件")
    create_message(acc, subject="普通邮件")
    with session_scope() as db:
        db.add(MessageLabel(message_id=tagged, label_id=label_id))
    data = client.get("/api/messages", params={"kind": f"label-{label_id}", "account_id": acc}).json()
    assert [m["subject"] for m in data["items"]] == ["带标签的邮件"]
    assert data["current_label"] == {"id": label_id, "name": "项目"}
    nav = client.get("/api/messages/nav").json()                                         # 侧栏
    assert {"id": label_id, "account_id": acc, "name": "项目", "short": "项目"} in nav["labels"]
    inbox = client.get("/api/messages", params={"kind": "inbox", "account_id": acc}).json()
    labels = {m["subject"]: m["labels"] for m in inbox["items"]}                         # 行内标签
    assert labels["带标签的邮件"] == [{"id": label_id, "name": "项目"}] and labels["普通邮件"] == []


def test_set_labels_updates_locally_and_enqueues(client):
    token = login(client)
    acc = create_account()
    gmail_labels.sync_label_list(acc, ["A", "B"])
    with session_scope() as db:
        ids = dict(db.execute(select(Label.name, Label.id).where(Label.account_id == acc)).all())
    mid = create_message(acc, uid=321)
    with session_scope() as db:
        db.add(MessageLabel(message_id=mid, label_id=ids["A"]))

    resp = api_post(client, f"/api/messages/{mid}/labels", token, {"label_ids": [ids["B"]]})
    assert resp.json() == {"ok": True, "labels": [{"id": ids["B"], "name": "B"}]}
    assert _labels_of(acc) == {321: {"B"}}
    with session_scope() as db:
        ops = sorted(db.execute(
            select(PendingOp.op, PendingOp.uids, PendingOp.arg).where(PendingOp.account_id == acc)
        ).all())
    assert ops == [("add_label", "321", "B"), ("remove_label", "321", "A")]


def test_detail_lists_account_labels(client):
    login(client)
    acc = create_account()
    gmail_labels.sync_label_list(acc, ["出差"])
    mid = create_message(acc)
    data = client.get(f"/api/messages/{mid}").json()
    assert [lb["name"] for lb in data["account_labels"]] == ["出差"]
    assert data["thread_labels"] == []
