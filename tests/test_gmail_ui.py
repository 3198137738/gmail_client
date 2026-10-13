"""Gmail 式界面的接口：详情页位置导航、写邮件 / 发送、收件人显示。"""
from __future__ import annotations

from datetime import datetime, timedelta

import app.routes.send as send_routes
import app.services.outgoing as outgoing
from app import serializers as ser
from app.services.smtp_service import SendResult, SMTPSendError
from tests.helpers import create_account, create_message, login


# ---- 详情页：第 N 封，共 M 封 / 较新 / 较旧 -------------------------------

def test_detail_position_and_neighbors(client):
    login(client)
    acc = create_account()
    base = datetime(2026, 2, 1, 12, 0)
    ids = [create_message(acc, subject=f"pos-{i}", date=base - timedelta(hours=i)) for i in range(5)]
    back = f"/messages/?kind=inbox&account_id={acc}&q=&page=1&page_size=25"

    data = client.get(f"/api/messages/{ids[2]}", params={"back": back}).json()
    assert data["back_url"] == back
    assert data["position"] == {"position": 3, "total": 5, "newer_id": ids[1], "older_id": ids[3]}

    first = client.get(f"/api/messages/{ids[0]}", params={"back": back}).json()
    assert first["position"]["position"] == 1 and first["position"]["total"] == 5
    assert first["position"]["newer_id"] is None  # 第一封没有更新的邮件


def test_detail_position_respects_unread_filter(client):
    login(client)
    acc = create_account()
    a = create_message(acc, subject="u-a", is_read=False, date=datetime(2026, 3, 3))
    create_message(acc, subject="u-b", is_read=True, date=datetime(2026, 3, 2))
    c = create_message(acc, subject="u-c", is_read=False, date=datetime(2026, 3, 1))
    back = f"/messages/?kind=inbox&account_id={acc}&q=&page=1&page_size=25&unread=1"
    pos = client.get(f"/api/messages/{a}", params={"back": back}).json()["position"]
    # 打开后 a 已标为已读，但仍算在内；已读的 b 不在「仅看未读」中
    assert pos["position"] == 1 and pos["total"] == 2
    assert pos["older_id"] == c


def test_detail_rejects_external_back_url(client):
    login(client)
    mid = create_message(create_account(), folder_kind="inbox")
    data = client.get(f"/api/messages/{mid}", params={"back": "https://evil.example/"}).json()
    assert data["back_url"] == "/messages/?kind=inbox"


# ---- 写邮件 / 发送 ---------------------------------------------------------

def _send_form(account_id: int, **extra) -> dict:
    data = {
        "account_id": str(account_id),
        "to": "friend@example.com",
        "subject": "保留我的主题",
        "body_text": "正文内容",
        "body_html": "<div>正文内容</div>",
    }
    data.update(extra)
    return data


def _send(client, token, data, **kwargs):
    return client.post("/api/compose/send", data=data, headers={"X-CSRF-Token": token}, **kwargs)


def test_send_failure_returns_error(client, monkeypatch):
    token = login(client)
    acc = create_account()

    def fail(**kwargs):
        raise SMTPSendError("SMTP authentication failed")

    monkeypatch.setattr(outgoing, "send_email", fail)
    resp = _send(client, token, _send_form(acc))
    # 失败时返回 400 和原因；已填写的内容（含附件）由前端保留
    assert resp.status_code == 400
    assert "SMTP authentication failed" in resp.json()["detail"]


def test_send_success_returns_sent_record(client, monkeypatch):
    token = login(client)
    acc = create_account()
    monkeypatch.setattr(outgoing, "send_email", lambda **kwargs: SendResult("<id@example.com>"))

    resp = _send(client, token, _send_form(acc))
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True and data["status"] == "sent" and data["error"] is None
    detail = client.get(f"/api/sent/{data['sent_id']}").json()
    assert detail["subject"] == "保留我的主题" and detail["status"] == "sent"


def test_send_route_runs_in_threadpool():
    """发信是阻塞的 smtplib 调用，路由必须是同步函数，否则会卡住整个事件循环。"""
    import inspect

    assert not inspect.iscoroutinefunction(send_routes.send)


def test_session_passes_undo_seconds(client):
    """撤销发送的等待秒数通过 /api/session 传给前端（默认 5 秒）。"""
    assert client.get("/api/session").json()["config"]["undo_send_seconds"] == 5


def test_partially_refused_send_is_reported(client, monkeypatch):
    token = login(client)
    acc = create_account()
    monkeypatch.setattr(
        outgoing, "send_email",
        lambda **kwargs: SendResult("<p@example.com>", refused={"bad@example.com": "550 No such user"}),
    )
    resp = _send(client, token, _send_form(acc))
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "partial"
    assert "bad@example.com" in data["error"] and "550 No such user" in data["error"]

    items = client.get("/api/sent").json()["items"]
    assert any(it["source"] == "local" and it["id"] == data["sent_id"] and it["status"] == "partial" for it in items)
    detail = client.get(f"/api/sent/{data['sent_id']}").json()
    assert detail["status"] == "partial" and "550 No such user" in detail["error"]


def test_smtp_returns_refused_recipients(monkeypatch):
    from app.crypto import encrypt_password
    from app.services import smtp_service

    class FakeSMTP:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def login(self, *a):
            pass

        def send_message(self, msg, from_addr, to_addrs):
            return {"bad@example.com": (550, b"5.1.1 No such user")}

    monkeypatch.setattr(smtp_service.smtplib, "SMTP_SSL", FakeSMTP)
    result = smtp_service.send_email(
        from_email="me@gmail.com", encrypted_password=encrypt_password("app-password"), from_name=None,
        to="ok@example.com, bad@example.com", cc=None, bcc=None, subject="s", body_text="b", body_html=None,
    )
    assert result.message_id
    assert result.refused == {"bad@example.com": "550 5.1.1 No such user"}


def test_same_name_uploads_are_both_attached(client, monkeypatch):
    token = login(client)
    acc = create_account()
    seen = {}

    def fake_send(**kwargs):
        seen["files"] = [(p.name, p.read_bytes()) for p in kwargs["attachments"]]
        return SendResult("<a@example.com>")

    monkeypatch.setattr(outgoing, "send_email", fake_send)
    files = [
        ("attachments", ("image.png", b"first", "image/png")),
        ("attachments", ("image.png", b"second", "image/png")),
    ]
    resp = _send(client, token, _send_form(acc), files=files)
    assert resp.status_code == 200
    assert seen["files"] == [("image.png", b"first"), ("image_1.png", b"second")]


def test_compose_reply_prefill(client):
    login(client)
    acc = create_account("me@example.com")
    mid = create_message(acc, subject="原主题", from_addr="Alice <alice@example.com>",
                         to_addr="me@example.com, bob@example.com")
    data = client.get("/api/compose", params={"reply_to": mid, "mode": "all"}).json()
    assert acc in [a["id"] for a in data["accounts"]]
    pf = data["prefill"]
    assert pf["subject"] == "Re: 原主题" and pf["to"] == "alice@example.com"
    assert pf["cc"] == "bob@example.com"   # 回复全部：不含自己
    assert pf["is_reply"] and pf["reply_all"]


# ---- 收件人显示 ------------------------------------------------------------

def test_recipients_shows_me():
    value = "zhang@example.com, Li Na <li@example.com>"
    assert ser.recipients(value, "zhang@example.com") == "我、Li Na"


def test_detail_shows_me_in_recipients(client):
    login(client)
    acc = create_account("zhang@example.com")
    mid = create_message(acc, to_addr="zhang@example.com, Li Na <li@example.com>")
    item = client.get(f"/api/messages/{mid}").json()["thread"][0]
    assert item["to_short"] == "我、Li Na"
    assert item["to_full"] == "zhang@example.com, Li Na <li@example.com>"


def test_snippet_collapses_whitespace():
    assert ser.snippet("a\n\n  b\tc", 200) == "a b c"
    assert ser.snippet("x" * 300, 10) == "x" * 10
    assert ser.snippet(None) == ""


def test_addresses_with_unquoted_at_sign_in_name():
    """显示名里带 @ 且没加引号（Python 3.13 严格模式会解析失败）时也要正确显示，且不重复。"""
    from app.addresses import format_address, parse_list

    raw = "service@paypal.co.uk <service@paypal.co.uk>"
    assert ser.addr_name(raw) == "service@paypal.co.uk"
    assert ser.addr_email(raw) == "service@paypal.co.uk"
    assert ser.addr_list(raw) == "service@paypal.co.uk"
    assert ser.recipients("me@x.com <me@x.com>", "me@x.com") == "我"
    assert parse_list('"Smith, John" <j@x.com>, b@x.com') == [("Smith, John", "j@x.com"), ("", "b@x.com")]
    assert format_address("Smith, John", "j@x.com") == '"Smith, John" <j@x.com>'
    assert format_address("j@x.com", "J@X.com") == "J@X.com"


def test_parser_stores_addresses_without_duplicate_names():
    from email.message import EmailMessage

    from app.services.email_parser import parse_email

    msg = EmailMessage()
    msg["From"] = '"service@paypal.co.uk" <service@paypal.co.uk>'
    msg["To"] = '"Smith, John" <j@x.com>'
    msg["Subject"] = "x"
    msg.set_content("x")
    parsed = parse_email(msg.as_bytes(), account_id=950, uid=1)
    assert parsed.from_addr == "service@paypal.co.uk"
    assert parsed.to_addr == '"Smith, John" <j@x.com>'
