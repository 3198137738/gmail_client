"""屏蔽发件人页的「永久删除」：把该发件人在 Gmail 上的全部邮件彻底清除，屏蔽保留。"""
from __future__ import annotations

from contextlib import contextmanager

import pytest
from sqlalchemy import select

import app.routes.senders as senders_routes
from app.database import session_scope
from app.models import AuditLog, BlockedSender, Message
from app.services import imap_purge
from tests.helpers import api_post, create_account, create_message, login

SENDER = "spam@bad.example"


class FakeGmail:
    """按 Gmail 的行为模拟：SEARCH FROM 子串匹配、MOVE 换 UID、EXPUNGE 真删。"""

    def __init__(self, folders: dict[str, dict[int, str]], flags: dict[str, bytes], capabilities=(b"MOVE",)):
        self.folders = folders
        self.flags = flags
        self.capabilities = capabilities
        self.selected: str | None = None
        self.calls: list[tuple] = []
        self._next_uid = 1000

    def list_folders(self):
        return [
            ((b"\\HasNoChildren", self.flags[name]) if name in self.flags else (b"\\HasNoChildren",), b"/", name)
            for name in self.folders
        ]

    def select_folder(self, folder, readonly=True):
        self.selected = folder
        self.calls.append(("select", folder))
        return {}

    def search(self, criteria):
        assert criteria[0] == "FROM"
        needle = criteria[1].lower()
        return [uid for uid, frm in self.folders[self.selected].items() if needle in frm.lower()]

    def fetch(self, uids, items):
        assert items == ["BODY.PEEK[HEADER.FIELDS (FROM)]"]
        msgs = self.folders[self.selected]
        return {
            uid: {b"BODY[HEADER.FIELDS (FROM)]": f"From: {msgs[uid]}\r\n\r\n".encode()}
            for uid in uids if uid in msgs
        }

    def has_capability(self, cap):
        return cap.encode() in self.capabilities

    def move(self, uids, target):
        self.calls.append(("move", self.selected, list(uids), target))
        src = self.folders[self.selected]
        for uid in uids:
            self.folders[target][self._next_uid] = src.pop(uid)
            self._next_uid += 1

    def copy(self, uids, target):
        self.calls.append(("copy", self.selected, list(uids), target))
        for uid in uids:
            self.folders[target][self._next_uid] = self.folders[self.selected][uid]
            self._next_uid += 1

    def delete_messages(self, uids):
        self.calls.append(("delete", self.selected, list(uids)))

    def uid_expunge(self, uids):
        self.calls.append(("expunge", self.selected, list(uids)))
        for uid in uids:
            self.folders[self.selected].pop(uid, None)


def _gmail() -> FakeGmail:
    return FakeGmail(
        folders={
            "INBOX": {1: f"Spammer <{SENDER}>", 2: "Friend <friend@ok.example>"},
            "[Gmail]/所有邮件": {
                11: f"Spammer <{SENDER}>",
                12: "friend@ok.example",
                13: f"Evil <x{SENDER}>",          # 子串命中但不是同一个地址，不能删
                14: SENDER.upper(),               # 大小写不同，是同一个地址
            },
            "[Gmail]/垃圾邮件": {21: SENDER},
            "[Gmail]/已删除邮件": {31: f"Spammer <{SENDER}>", 32: "friend@ok.example"},
        },
        flags={
            "[Gmail]/所有邮件": b"\\All",
            "[Gmail]/垃圾邮件": b"\\Junk",
            "[Gmail]/已删除邮件": b"\\Trash",
        },
    )


@pytest.fixture()
def fake(monkeypatch):
    holder: dict = {}

    @contextmanager
    def fake_imap_client(email, password, **kwargs):
        yield holder["client"]

    monkeypatch.setattr(imap_purge, "_imap_client", fake_imap_client)
    return holder


def test_purge_moves_to_trash_then_expunges_exact_sender_only(fake):
    fake["client"] = gm = _gmail()
    acc = create_account()
    with session_scope() as db:
        db.add(BlockedSender(account_id=acc, sender_email=SENDER))
    local_spam = create_message(acc, from_addr=f"Spammer <{SENDER}>", folder_kind="junk", folder="[Gmail]/垃圾邮件")
    local_friend = create_message(acc, from_addr="friend@ok.example")

    result = imap_purge.purge_sender_messages(acc, SENDER)

    assert result["ok"], result
    assert result["trashed"] == 3          # 所有邮件 11、14 + 垃圾邮件 21
    assert result["purged"] == 4           # 垃圾箱里原有的 31 + 移进来的 3 封
    assert result["local"] == 1
    # 所有邮件里只剩别人的和「xspam@」那封；收件箱没被单独处理（它包含在所有邮件里）
    assert set(gm.folders["[Gmail]/所有邮件"]) == {12, 13}
    assert gm.folders["[Gmail]/垃圾邮件"] == {}
    assert set(gm.folders["[Gmail]/已删除邮件"]) == {32}
    assert ("select", "INBOX") not in gm.calls
    assert ("move", "[Gmail]/所有邮件", [11, 14], "[Gmail]/已删除邮件") in gm.calls
    assert any(c[0] == "expunge" and c[1] == "[Gmail]/已删除邮件" and len(c[2]) == 4 for c in gm.calls)

    with session_scope() as db:
        assert db.get(Message, local_spam) is None
        assert db.get(Message, local_friend) is not None
        # 屏蔽记录保留
        assert db.execute(
            select(BlockedSender).where(BlockedSender.account_id == acc, BlockedSender.sender_email == SENDER)
        ).scalar_one_or_none() is not None


def test_purge_without_gmail_special_folders_expunges_inbox(fake):
    fake["client"] = gm = FakeGmail(
        folders={"INBOX": {1: SENDER, 2: "friend@ok.example"}}, flags={}
    )
    acc = create_account()
    result = imap_purge.purge_sender_messages(acc, SENDER)
    assert result["ok"] and result["purged"] == 1 and result["trashed"] == 1
    assert gm.folders["INBOX"] == {2: "friend@ok.example"}
    assert ("delete", "INBOX", [1]) in gm.calls


def test_purge_reports_busy_when_account_locked(fake):
    from app.services.imap_client import _account_lock

    fake["client"] = _gmail()
    acc = create_account()
    lock = _account_lock(acc)
    lock.acquire()
    try:
        result = imap_purge.purge_sender_messages(acc, SENDER, lock_timeout=0.05)
    finally:
        lock.release()
    assert not result["ok"] and result["error"] == "busy" and not result["folders"]
    assert imap_purge.describe_purge(result) == "账号正在同步，请稍后再试"


def test_describe_purge():
    assert imap_purge.describe_purge({"ok": True, "purged": 7, "folders": {"x": {}}, "error": None}) == "已永久删除 7 封"
    partial = {"ok": False, "purged": 2, "folders": {"x": {"error": "boom"}}, "error": "boom"}
    assert imap_purge.describe_purge(partial) == "已永久删除 2 封，部分文件夹操作失败：boom"
    assert "连接 Gmail 失败" in imap_purge.describe_purge({"ok": False, "folders": {}, "error": "IMAP network error"})


# ---- 路由 -----------------------------------------------------------------
def _blocked_id(acc: int) -> int:
    with session_scope() as db:
        row = BlockedSender(account_id=acc, sender_email=SENDER)
        db.add(row)
        db.flush()
        return int(row.id)


def test_purge_route_keeps_block_and_shows_result(client, monkeypatch):
    token = login(client)
    acc = create_account()
    bid = _blocked_id(acc)
    calls: list[tuple] = []

    def fake_purge(account_id, sender_email, **kw):
        calls.append((account_id, sender_email))
        return {"ok": True, "trashed": 3, "purged": 5, "local": 0,
                "folders": {"[Gmail]/已删除邮件": {"count": 5, "error": None}}, "error": None}

    monkeypatch.setattr(senders_routes, "purge_sender_messages", fake_purge)
    listing = client.get("/api/senders", params={"account_id": acc}).json()
    assert bid in [row["id"] for row in listing["blocked"]]

    resp = api_post(client, f"/api/senders/blocked/{bid}/purge", token)
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "message": f"{SENDER}：已永久删除 5 封"}
    assert calls == [(acc, SENDER)]
    with session_scope() as db:
        assert db.get(BlockedSender, bid) is not None   # 不是解除屏蔽
        last = db.execute(select(AuditLog).order_by(AuditLog.id.desc()).limit(1)).scalar_one()
        assert last.event == "sender_purged" and SENDER in (last.detail or "") and "5 封" in last.detail


def test_purge_route_busy_shows_error_without_audit(client, monkeypatch):
    token = login(client)
    acc = create_account()
    bid = _blocked_id(acc)
    monkeypatch.setattr(
        senders_routes, "purge_sender_messages",
        lambda *a, **k: {"ok": False, "trashed": 0, "purged": 0, "local": 0, "folders": {}, "error": "busy"},
    )
    with session_scope() as db:
        before = db.execute(select(AuditLog.id).order_by(AuditLog.id.desc()).limit(1)).scalar()
    data = api_post(client, f"/api/senders/blocked/{bid}/purge", token).json()
    assert data["ok"] is False and "账号正在同步" in data["message"]
    with session_scope() as db:
        after = db.execute(select(AuditLog.id).order_by(AuditLog.id.desc()).limit(1)).scalar()
    assert before == after


def test_purge_route_404_for_unknown_row(client):
    token = login(client)
    resp = api_post(client, "/api/senders/blocked/999999/purge", token)
    assert resp.status_code == 404


def test_batch_purge_runs_in_background(client, monkeypatch, _no_background_network):
    token = login(client)
    acc = create_account()
    bid = _blocked_id(acc)
    with session_scope() as db:
        row = BlockedSender(account_id=acc, sender_email="ads@bad.example")
        db.add(row)
        db.flush()
        bid2 = int(row.id)

    resp = api_post(client, "/api/senders/blocked/purge", token, json={"ids": [bid, bid2, 99999]})
    assert resp.status_code == 200 and resp.json()["queued"] == 2
    name, args, _kw = _no_background_network[-1]
    assert name == "_purge_many"
    targets, ip = args
    assert {(t[0], t[1]) for t in targets} == {(acc, SENDER), (acc, "ads@bad.example")}

    # 后台任务：逐个永久删除、记审计、推送结果
    def fake_purge(account_id, sender_email, **kw):
        if sender_email == SENDER:
            return {"ok": True, "trashed": 1, "purged": 4, "local": 0,
                    "folders": {"T": {"count": 4, "error": None}}, "error": None}
        return {"ok": False, "trashed": 0, "purged": 0, "local": 0, "folders": {}, "error": "busy"}

    events: list[dict] = []
    monkeypatch.setattr(senders_routes, "purge_sender_messages", fake_purge)
    monkeypatch.setattr(senders_routes, "publish_purge_done", lambda **kw: events.append(kw))
    senders_routes._purge_many(targets, ip)
    assert events == [{"senders": 2, "failed": 1, "purged": 4}]
    with session_scope() as db:
        details = [r.detail for r in db.execute(select(AuditLog).where(AuditLog.event == "sender_purged")).scalars()]
        assert any(d.startswith(SENDER) and "已永久删除 4 封" in d for d in details)
        assert not any(d.startswith("ads@bad.example") for d in details)

    assert api_post(client, "/api/senders/blocked/purge", token, json={"ids": []}).status_code == 404
