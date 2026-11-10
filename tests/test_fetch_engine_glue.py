"""下载引擎的 Python 封装（用假引擎，不需要编译产物）：头部先入库并推送页面、正文到达后补齐、
正文接口的占位、引擎出错时退回、凭据变化时重建。"""
from __future__ import annotations

import types

import pytest
from sqlalchemy import select

from app import native
from app.database import session_scope
from app.models import Message
from app.services import events, fetch_engine, telegram_service
from app.services.imap_service import _sync_one_folder
from tests.helpers import create_account, login
from tests.test_backfill import FakeMailbox, _count, _state, no_notify  # noqa: F401
from app.services.fastmime_check import PNG


def _raw(uid: int, *, html: bool = True) -> bytes:
    from email.message import EmailMessage

    msg = EmailMessage()
    msg["From"] = f"Shop <shop{uid}@example.com>"
    msg["To"] = "me@example.com"
    msg["Subject"] = f"验证码 {100000 + uid}"
    msg["Message-ID"] = f"<m{uid}@example.com>"
    msg.set_content(f"您的验证码是 {100000 + uid}，5 分钟内有效")
    if html:
        msg.add_alternative(f"<p>code <b>{100000 + uid}</b></p>", subtype="html")
        msg.get_payload()[1].add_related(PNG, maintype="image", subtype="png", cid=f"<img{uid}>")
    return msg.as_bytes()


def _header(raw: bytes) -> bytes:
    head, _sep, _body = raw.partition(b"\r\n\r\n")
    return head + b"\r\n\r\n"


class FakeEngine:
    """模拟 C++ 引擎：记录调用，按脚本回调头部和正文。"""

    created: list["FakeEngine"] = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.closed = False
        self.mails: dict[int, bytes] = {}
        self.fail = None
        self.too_many = False
        self.calls: list[tuple] = []
        FakeEngine.created.append(self)

    def has_capability(self, cap):
        return cap == "X-GM-EXT-1"

    def close(self):
        self.closed = True

    def fetch_new(self, last_uid, max_messages, on_headers, on_body):
        self.calls.append(("fetch_new", last_uid, max_messages))
        if self.fail:
            raise RuntimeError(self.fail)
        if self.too_many:
            return {"too_many": True}
        new = {u: r for u, r in self.mails.items() if u > last_uid}
        on_headers([
            {
                "uid": u, "flags": [b"\\Seen"] if u % 2 else [], "internaldate": b"10-Nov-2026 11:30:19 +0000",
                "size": len(new[u]), "thrid": 5000 + u, "labels": [b"\\Inbox"], "header": _header(new[u]),
            }
            for u in sorted(new)
        ])
        for u in sorted(new):
            on_body(u, new[u])
        return {"too_many": False, "count": len(new), "connections": 3, "compressed": True}


@pytest.fixture(autouse=True)
def _fake_module(monkeypatch):
    FakeEngine.created.clear()
    native.imapfetch = types.SimpleNamespace(Engine=FakeEngine)
    monkeypatch.setattr(fetch_engine, "_disabled_reason", None)
    monkeypatch.setattr(fetch_engine, "FETCH_ENGINE_ENABLED", True)
    fetch_engine.drop_engine()
    yield
    fetch_engine.drop_engine()
    native.imapfetch = None


@pytest.fixture
def events_log(monkeypatch):
    log = []
    monkeypatch.setattr(events.event_bus, "publish", lambda ev: log.append(ev))
    monkeypatch.setattr(fetch_engine, "submit_bg", lambda fn, *a, **k: log.append(("bg", fn.__name__, a, k)))
    return log


def _prepared(n: int = 3):
    acc = create_account()
    box = FakeMailbox(n)
    _sync_one_folder(box, acc, "INBOX", "inbox")
    return acc, box


def test_headers_first_then_bodies(no_notify, events_log):
    acc, box = _prepared()
    engine = None

    class Spy(FakeEngine):
        def fetch_new(self, last_uid, max_messages, on_headers, on_body):
            def headers(records):
                on_headers(records)
                # 头部入库后、正文到达前：邮件已在库里且标记正文待取，页面已收到 new_mail
                with session_scope() as db:
                    rows = db.execute(select(Message).where(Message.account_id == acc, Message.uid.in_([4, 5]))).scalars().all()
                    assert len(rows) == 2 and all(r.body_pending and r.body_html is None for r in rows)
                    assert {r.subject for r in rows} == {"验证码 100004", "验证码 100005"}
                    assert all(r.otp_code == str(100000 + r.uid) for r in rows)   # 主题里的验证码已识别
                    assert all(r.thread_id == 5000 + r.uid for r in rows)
                assert [e["type"] for e in events_log] == ["new_mail"]
                assert events_log[0]["inbox"] == 2 and len(events_log[0]["preview"]) == 2
            return super().fetch_new(last_uid, max_messages, headers, on_body)

    native.imapfetch = types.SimpleNamespace(Engine=Spy)
    engine_mails = {4: _raw(4), 5: _raw(5)}
    Spy.created.clear()

    def creator(**kw):
        e = Spy(**kw)
        e.mails = engine_mails
        return e

    native.imapfetch = types.SimpleNamespace(Engine=creator)
    result = fetch_engine.fetch_new_mail(acc, "u@example.com", "app-password", "INBOX", "inbox", uidvalidity=box.uidvalidity)
    assert result == {"saved": 2, "scanned": 2, "folder": "INBOX", "folder_kind": "inbox"}
    assert _count(acc) == 5 and _state(acc).last_uid == 5

    with session_scope() as db:
        rows = db.execute(select(Message).where(Message.account_id == acc, Message.uid.in_([4, 5]))).scalars().all()
        assert all(not r.body_pending and "<b>" in r.body_html and r.body_text.startswith("您的验证码") for r in rows)
        assert all(len(r.attachments) == 1 and r.attachments[0].is_inline for r in rows)
        assert all(r.is_read == bool(r.uid % 2) for r in rows)
    kinds = [e["type"] for e in events_log if isinstance(e, dict)]
    assert kinds == ["new_mail", "mail_changed", "mail_changed"]
    # 正文都到齐后才发 Telegram
    assert [e for e in events_log if isinstance(e, tuple)] == [("bg", "notify_new_mail", (acc,), {"inbox": 2})]
    # 引擎常驻、按凭据复用
    assert len(Spy.created) == 1 and Spy.created[0].kwargs["user"] == "u@example.com" and Spy.created[0].kwargs["password"] == "app-password"


def test_engine_error_falls_back_and_is_recreated(no_notify, events_log):
    acc, box = _prepared()
    FakeEngine.created.clear()
    first = fetch_engine.get_engine(acc, "u@example.com", "app-password", "INBOX")
    first.engine.fail = "connection lost"
    assert fetch_engine.fetch_new_mail(acc, "u@example.com", "app-password", "INBOX", "inbox", uidvalidity=box.uidvalidity) is None
    assert first.engine.closed and _count(acc) == 3
    # 下次推送重新创建
    second = fetch_engine.get_engine(acc, "u@example.com", "app-password", "INBOX")
    assert second is not first and len(FakeEngine.created) == 2
    # 凭据变化也重建；全部丢弃后关闭
    third = fetch_engine.get_engine(acc, "u@example.com", "new-password", "INBOX")
    assert third is not second and second.engine.closed
    fetch_engine.drop_engine()
    assert third.engine.closed


def test_too_many_or_unsynced_returns_none(no_notify):
    acc = create_account()
    box = FakeMailbox(2)
    # 还没首次同步
    assert fetch_engine.fetch_new_mail(acc, "u@example.com", "app-password", "INBOX", "inbox", uidvalidity=box.uidvalidity) is None
    _sync_one_folder(box, acc, "INBOX", "inbox")
    handle = fetch_engine.get_engine(acc, "u@example.com", "app-password", "INBOX")
    handle.engine.too_many = True
    assert fetch_engine.fetch_new_mail(acc, "u@example.com", "app-password", "INBOX", "inbox", uidvalidity=box.uidvalidity) is None
    native.imapfetch = None
    assert not fetch_engine.available()
    assert fetch_engine.get_engine(acc, "u@example.com", "app-password", "INBOX") is None


def test_pending_body_placeholder_and_detail_flag(client, no_notify):
    token = login(client)
    acc, box = _prepared()
    with session_scope() as db:
        msg = Message(account_id=acc, folder="INBOX", folder_kind="inbox", uid=99, subject="头部先到", from_addr="a@example.com", body_pending=True)
        db.add(msg)
        db.flush()
        mid = int(msg.id)
    detail = client.get(f"/api/messages/{mid}").json()
    assert detail["thread"][0]["body_pending"] is True
    body = client.get(f"/messages/{mid}/body").text
    assert "正文正在下载" in body
    from app.services.imap_service import complete_message_body

    assert complete_message_body(acc, "INBOX", "inbox", 99, _raw(99)) == mid
    assert client.get(f"/api/messages/{mid}").json()["thread"][0]["body_pending"] is False
    assert "100099" in client.get(f"/messages/{mid}/body").text
    assert complete_message_body(acc, "INBOX", "inbox", 12345, _raw(1)) is None


def test_regular_sync_repairs_stuck_pending_bodies(no_notify, monkeypatch):
    from datetime import datetime, timedelta

    from app.services import imap_service

    acc, box = _prepared()
    # 模拟头部先行入库、正文一直没到的邮件（引擎中途出错）
    with session_scope() as db:
        row = db.execute(select(Message).where(Message.account_id == acc, Message.uid == 3)).scalar_one()
        row.body_pending = True
        row.body_html = None
        row.body_text = None
        row.created_at = datetime.now() - timedelta(minutes=5)
    monkeypatch.setattr(imap_service, "PENDING_BODY_GRACE", timedelta(minutes=1))
    _sync_one_folder(box, acc, "INBOX", "inbox")
    with session_scope() as db:
        row = db.execute(select(Message).where(Message.account_id == acc, Message.uid == 3)).scalar_one()
        assert not row.body_pending and row.body_text == "body 3"
