"""Outlook 邮箱：Microsoft 授权、refresh token 轮换、按服务商连接 IMAP / SMTP、远程搜索、会话。"""
from __future__ import annotations

import base64
import json
from contextlib import contextmanager
from datetime import date, datetime
from urllib.parse import parse_qs, urlparse

import pytest
from sqlalchemy import select

from app.crypto import decrypt_password, encrypt_password
from app.database import session_scope
from app.models import Account
from app.services import imap_client, imap_service, mail_providers, ms_oauth, smtp_service
from app.services.email_parser import parse_email
from tests.helpers import api_post, create_account, create_message, login

CLIENT_ID = "11111111-2222-3333-4444-555555555555"


@pytest.fixture()
def configured(monkeypatch):
    monkeypatch.setattr(ms_oauth, "MICROSOFT_OAUTH_CLIENT_ID", CLIENT_ID)
    monkeypatch.setattr(ms_oauth, "MICROSOFT_OAUTH_CLIENT_SECRET", "msecret")
    ms_oauth._cache.clear()
    ms_oauth._rotated.clear()
    yield
    ms_oauth._cache.clear()
    ms_oauth._rotated.clear()


def _id_token(claims: dict) -> str:
    body = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    return f"header.{body}.signature"


_GOOD_CLAIMS = {
    "aud": CLIENT_ID,
    "iss": "https://login.microsoftonline.com/9188040d-6c67-4c5b-b112-36a304b66dad/v2.0",
    "email": "Me@Outlook.com",
    "name": "张三",
}


def test_authorize_url(configured):
    url = ms_oauth.authorize_url("https://mail.example/accounts/oauth/microsoft/callback", "st", "ch")
    parsed = urlparse(url)
    assert parsed.netloc == "login.microsoftonline.com" and parsed.path == "/common/oauth2/v2.0/authorize"
    qs = parse_qs(parsed.query)
    assert "https://outlook.office.com/IMAP.AccessAsUser.All" in qs["scope"][0]
    assert "offline_access" in qs["scope"][0] and qs["code_challenge_method"] == ["S256"]


def test_exchange_code_validates_id_token(configured, monkeypatch):
    monkeypatch.setattr(ms_oauth, "_post", lambda data: {
        "refresh_token": "rt", "access_token": "at", "expires_in": 3600,
        "scope": "https://outlook.office.com/IMAP.AccessAsUser.All https://outlook.office.com/SMTP.Send",
        "id_token": _id_token(_GOOD_CLAIMS),
    })
    assert ms_oauth.exchange_code("c", "https://x/cb", "v") == ("me@outlook.com", "rt", "张三")

    for bad in (dict(_GOOD_CLAIMS, aud="other"), dict(_GOOD_CLAIMS, iss="https://evil.example/v2.0")):
        monkeypatch.setattr(ms_oauth, "_post", lambda data, bad=bad: {
            "refresh_token": "rt", "scope": "IMAP.AccessAsUser.All", "id_token": _id_token(bad),
        })
        with pytest.raises(ms_oauth.OAuthError):
            ms_oauth.exchange_code("c", "https://x/cb", "v")


def test_refresh_token_rotation_is_persisted(configured, monkeypatch):
    acc = create_account("rotate@outlook.com")
    with session_scope() as db:
        db.get(Account, acc).encrypted_password = encrypt_password("msoauth:rt-1")

    calls = []

    def fake_post(data):
        calls.append(data["refresh_token"])
        n = len(calls)
        return {"access_token": f"at-{n}", "refresh_token": f"rt-{n + 1}", "expires_in": 3600}

    monkeypatch.setattr(ms_oauth, "_post", fake_post)
    assert ms_oauth.access_token_for_secret("msoauth:rt-1", "rotate@outlook.com") == "at-1"
    with session_scope() as db:
        assert decrypt_password(db.get(Account, acc).encrypted_password) == "msoauth:rt-2"
    # 仍持有旧凭据的连接：走缓存，过期后用最新的 refresh token 刷新
    assert ms_oauth.access_token_for_secret("msoauth:rt-1", "rotate@outlook.com") == "at-1"
    ms_oauth._cache.clear()
    assert ms_oauth.access_token_for_secret("msoauth:rt-1", "rotate@outlook.com") == "at-2"
    assert calls == ["rt-1", "rt-2"]
    with session_scope() as db:
        assert decrypt_password(db.get(Account, acc).encrypted_password) == "msoauth:rt-3"


def test_rotation_does_not_overwrite_reauthorized_account(configured, monkeypatch):
    acc = create_account("reauth@outlook.com")
    with session_scope() as db:
        db.get(Account, acc).encrypted_password = encrypt_password("msoauth:fresh")
    monkeypatch.setattr(ms_oauth, "_post", lambda data: {"access_token": "at", "refresh_token": "rt-new"})
    ms_oauth.access_token_for_secret("msoauth:stale", "reauth@outlook.com")
    with session_scope() as db:
        assert decrypt_password(db.get(Account, acc).encrypted_password) == "msoauth:fresh"


def test_provider_detection():
    assert mail_providers.key_for_secret("msoauth:x") == mail_providers.OUTLOOK
    assert mail_providers.key_for_secret("oauth2:x") == mail_providers.GMAIL
    assert mail_providers.key_for_secret("app-password") == mail_providers.GMAIL
    for email in ("a@outlook.com", "a@hotmail.co.uk", "a@live.cn", "a@msn.com"):
        assert mail_providers.is_outlook_address(email)
    assert not mail_providers.is_outlook_address("a@gmail.com")


def test_imap_connects_to_outlook_with_xoauth2(monkeypatch):
    seen = {}

    class FakeIMAP:
        def __init__(self, **kwargs):
            seen["host"] = kwargs["host"]

        def oauth2_login(self, user, token):
            seen["login"] = (user, token)

        def logout(self):
            pass

    monkeypatch.setattr(imap_client, "IMAPClient", FakeIMAP)
    monkeypatch.setattr(ms_oauth, "access_token_for_secret", lambda secret, email=None: "ms-token")
    with imap_client._imap_client("u@outlook.com", "msoauth:rt"):
        pass
    assert seen == {"host": "outlook.office365.com", "login": ("u@outlook.com", "ms-token")}


def test_imap_reports_revoked_microsoft_authorization(monkeypatch):
    class FakeIMAP:
        def __init__(self, **kwargs):
            pass

        def logout(self):
            pass

    def revoked(secret, email=None):
        raise ms_oauth.OAuthError("invalid_grant")

    monkeypatch.setattr(imap_client, "IMAPClient", FakeIMAP)
    monkeypatch.setattr(ms_oauth, "access_token_for_secret", revoked)
    with pytest.raises(imap_client.IMAPSyncError, match="Microsoft 授权已失效"):
        with imap_client._imap_client("u@outlook.com", "msoauth:rt"):
            pass


def test_smtp_uses_starttls_for_outlook(monkeypatch):
    seen = {}

    class FakeSMTP:
        def __init__(self, host, port, **k):
            seen["server"] = (host, port)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def ehlo(self):
            pass

        def starttls(self, context=None):
            seen["starttls"] = True

        def auth(self, mechanism, authobject):
            seen["auth"] = (mechanism, authobject())

        def send_message(self, msg, from_addr, to_addrs):
            return {}

    monkeypatch.setattr(smtp_service.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(smtp_service.smtplib, "SMTP_SSL", lambda *a, **k: pytest.fail("Outlook 不支持 465"))
    monkeypatch.setattr(ms_oauth, "access_token_for_secret", lambda secret, email=None: "tok")
    smtp_service.send_email(
        from_email="me@outlook.com", encrypted_password=encrypt_password("msoauth:rt"), from_name=None,
        to="you@example.com", cc=None, bcc=None, subject="s", body_text="b", body_html=None,
        attachments=[], use_ssl=True,
    )
    assert seen["server"] == ("smtp.office365.com", 587) and seen["starttls"]
    assert seen["auth"] == ("XOAUTH2", "user=me@outlook.com\x01auth=Bearer tok\x01\x01")


def test_microsoft_oauth_flow_creates_account(client, configured, monkeypatch, _no_background_network):
    login(client)
    resp = client.get("/accounts/oauth/microsoft/start", follow_redirects=False)
    assert resp.status_code == 303
    target = urlparse(resp.headers["location"])
    assert target.netloc == "login.microsoftonline.com"
    qs = parse_qs(target.query)
    assert qs["redirect_uri"] == ["http://testserver/accounts/oauth/microsoft/callback"]

    monkeypatch.setattr(ms_oauth, "exchange_code", lambda *a: ("new@outlook.com", "rt-ms", "李四"))
    resp = client.get(
        "/accounts/oauth/microsoft/callback", params={"code": "c", "state": qs["state"][0]}, follow_redirects=False
    )
    assert resp.status_code == 303 and resp.headers["location"] == "/accounts/"
    with session_scope() as db:
        acc = db.execute(select(Account).where(Account.email == "new@outlook.com")).scalar_one()
        assert decrypt_password(acc.encrypted_password) == "msoauth:rt-ms"
        assert acc.display_name == "李四"
    assert any(name == "sync_then_backfill" for name, *_ in _no_background_network)
    data = client.get("/api/accounts").json()
    assert data["flash"]["message"] == "已通过 Microsoft 授权添加 new@outlook.com"
    assert data["ms_oauth_configured"] is True
    row = next(a for a in data["accounts"] if a["email"] == "new@outlook.com")
    assert row["provider"] == "outlook" and row["oauth"] is True


def test_microsoft_callback_rejects_wrong_state(client, configured, monkeypatch):
    login(client)
    client.get("/accounts/oauth/microsoft/start", follow_redirects=False)
    monkeypatch.setattr(ms_oauth, "exchange_code", lambda *a: pytest.fail("must not exchange"))
    client.get("/accounts/oauth/microsoft/callback", params={"code": "x", "state": "forged"}, follow_redirects=False)
    assert client.get("/api/accounts").json()["flash"]["message"] == "授权请求已失效"


def test_outlook_password_accounts_rejected(client):
    token = login(client)
    resp = api_post(client, "/api/accounts", token, {"email": "x@hotmail.com", "password": "abcdefghijkl"})
    assert resp.status_code == 400 and "Microsoft" in resp.json()["detail"]
    resp = api_post(client, "/api/accounts/batch", token, {"accounts_text": "y@outlook.com----abcdefghijkl"})
    assert resp.json()["added"] == 0 and "Microsoft" in resp.json()["message"]


def test_to_imap_criteria():
    from app.services.remote_search import to_imap_criteria

    crit = to_imap_criteria('from:alice subject:"季度 报告" is:unread after:2026-01-02 发票')
    assert crit == ["FROM", "alice", "SUBJECT", "季度 报告", "UNSEEN", "SINCE", date(2026, 1, 2), "TEXT", "发票"]
    assert to_imap_criteria("") == ["ALL"]
    assert to_imap_criteria("to:bob has:attachment") == ["OR", "TO", "bob", "CC", "bob"]


def test_remote_search_outlook(client, monkeypatch):
    import app.services.remote_search as rs

    class FakeIMAP:
        def __init__(self):
            self.calls = []
            self.folder = None

        def list_folders(self):
            return [
                ((b"\\HasNoChildren",), b"/", "Inbox"),
                ((b"\\HasNoChildren", b"\\Archive"), b"/", "Archive"),
                ((b"\\HasNoChildren", b"\\Sent"), b"/", "Sent Items"),
            ]

        def select_folder(self, folder, readonly=False):
            self.folder = folder

        def search(self, criteria, charset=None):
            self.calls.append((self.folder, criteria, charset))
            return {"INBOX": [5], "Archive": [7], "Sent Items": []}[self.folder]

        def gmail_search(self, query):
            pytest.fail("Outlook 不支持 X-GM-RAW")

        def fetch(self, uids, items):
            day = {"INBOX": 3, "Archive": 9}[self.folder]
            return {
                uid: {
                    b"BODY[HEADER.FIELDS (FROM TO SUBJECT MESSAGE-ID)]":
                        f"Subject: {self.folder}\r\nMessage-ID: <{self.folder}@x>\r\n\r\n".encode(),
                    b"INTERNALDATE": datetime(2024, 1, day),
                    b"FLAGS": (),
                }
                for uid in uids
            }

    fake = FakeIMAP()

    @contextmanager
    def fake_client(email, password, **kw):
        yield fake

    monkeypatch.setattr(rs, "_imap_client", fake_client)
    login(client)
    acc = create_account("search@outlook.com")
    with session_scope() as db:
        db.get(Account, acc).encrypted_password = encrypt_password("msoauth:rt")
    local = create_message(acc, message_id="<INBOX@x>")

    data = client.get("/api/messages/remote-search", params={"q": "发票", "account_id": acc}).json()
    res = data["accounts"][0]
    assert res["provider"] == "outlook" and res["total"] == 2
    assert [i["subject"] for i in res["items"]] == ["Archive", "INBOX"]   # 按时间合并，最新的在前
    assert res["items"][1]["local_id"] == local
    assert res["items"][0]["gmail_url"] == "https://outlook.live.com/mail/0/"
    assert ("INBOX", ["TEXT", "发票"], "UTF-8") in fake.calls


def test_header_threads_group_replies(tmp_path):
    first = b"Message-ID: <Root@x>\r\nSubject: hi\r\n\r\nbody"
    reply = b"Message-ID: <r2@x>\r\nIn-Reply-To: <r1@x>\r\nReferences: <root@x> <r1@x>\r\nSubject: Re: hi\r\n\r\nb"
    roots = [parse_email(raw, 1, uid).thread_root for uid, raw in ((1, first), (2, reply))]
    assert roots == ["root@x", "root@x"]
    ids = {imap_service.header_thread_id(r) for r in roots}
    assert len(ids) == 1 and 0 < ids.pop() < 2 ** 63
    assert imap_service.header_thread_id(None) is None


def test_special_folder_name_fallback():
    class FakeIMAP:
        def list_folders(self):
            return [((b"\\HasNoChildren",), b"/", "Deleted Items"), ((b"\\HasNoChildren",), b"/", "Junk Email")]

    assert imap_client._discover_trash_folder(FakeIMAP()) == "Deleted Items"
    assert imap_client._discover_junk_folder(FakeIMAP()) == "Junk Email"
    assert imap_client._discover_sent_folder(FakeIMAP()) is None
