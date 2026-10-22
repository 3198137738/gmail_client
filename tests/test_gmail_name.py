"""邮箱姓名：从 Gmail 读取 Send-as 显示名称（应用专用密码账号按已发送邮件推断），可同步修改到 Gmail。"""
from __future__ import annotations

from datetime import datetime

from app.crypto import encrypt_password
from app.database import session_scope
from app.models import Account
from app.services import gmail_profile, google_oauth
from tests.helpers import api_post, create_account, create_message, login


def _make_oauth(account_id: int) -> None:
    with session_scope() as db:
        db.get(Account, account_id).encrypted_password = encrypt_password(google_oauth.make_secret("rt"))


def _account(client, acc: int) -> dict:
    return next(a for a in client.get("/api/accounts").json()["accounts"] if a["id"] == acc)


def test_app_password_account_name_from_sent(client):
    login(client)
    acc = create_account("sent-name@gmail.com")
    create_message(acc, folder_kind="sent", from_addr="旧名字 <sent-name@gmail.com>", date=datetime(2026, 1, 1))
    create_message(acc, folder_kind="sent", from_addr="张三 <SENT-NAME@gmail.com>", date=datetime(2026, 2, 1))
    create_message(acc, folder_kind="sent", from_addr="别名 <alias@example.com>", date=datetime(2026, 3, 1))
    create_message(acc, folder_kind="inbox", from_addr="李四 <sent-name@gmail.com>", date=datetime(2026, 4, 1))

    resp = client.get(f"/api/accounts/{acc}/gmail-name")
    assert resp.status_code == 200
    assert resp.json() == {"name": "张三", "source": "sent", "can_update": False}

    empty = create_account()
    assert client.get(f"/api/accounts/{empty}/gmail-name").json() == {"name": "", "source": "none", "can_update": False}


def test_oauth_account_fetch_and_update(client, monkeypatch):
    token = login(client)
    acc = create_account("oauth-name@gmail.com")
    _make_oauth(acc)
    monkeypatch.setattr(google_oauth, "access_token_for_secret", lambda secret: "at")
    calls = []

    def fake_request(secret, email, method="GET", body=None):
        calls.append((email, method, body))
        return {"displayName": body["displayName"] if body else "Gmail 姓名"}

    monkeypatch.setattr(gmail_profile, "_request", fake_request)
    assert client.get(f"/api/accounts/{acc}/gmail-name").json() == {
        "name": "Gmail 姓名", "source": "gmail", "can_update": True,
    }

    resp = api_post(client, f"/api/accounts/{acc}/update", token, {"display_name": "  新名字 ", "sync_gmail_name": True})
    assert resp.status_code == 200 and resp.json()["gmail_name"] == "新名字"
    assert calls[-1] == ("oauth-name@gmail.com", "PATCH", {"displayName": "新名字"})
    assert _account(client, acc)["display_name"] == "新名字"


def test_gmail_update_failure_keeps_local_name(client, monkeypatch):
    token = login(client)
    acc = create_account()
    api_post(client, f"/api/accounts/{acc}/update", token, {"display_name": "原名"})

    # 应用专用密码账号不能改 Gmail 姓名，整个保存不生效
    resp = api_post(client, f"/api/accounts/{acc}/update", token, {"display_name": "新名", "sync_gmail_name": True})
    assert resp.status_code == 400
    assert _account(client, acc)["display_name"] == "原名"

    _make_oauth(acc)

    def failing(*args, **kwargs):
        raise gmail_profile.GmailProfileError("当前授权没有修改 Gmail 设置的权限")

    monkeypatch.setattr(gmail_profile, "_request", failing)
    resp = api_post(client, f"/api/accounts/{acc}/update", token, {"display_name": "新名", "sync_gmail_name": True})
    assert resp.status_code == 400 and "权限" in resp.json()["detail"]
    assert _account(client, acc)["display_name"] == "原名"
    assert client.get(f"/api/accounts/{acc}/gmail-name").status_code == 502
