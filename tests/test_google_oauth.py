"""Google 账号授权（OAuth2）：授权流程、state 校验、令牌缓存、IMAP / SMTP 以 XOAUTH2 登录。"""
from __future__ import annotations

import base64
import hashlib
import json
from urllib.parse import parse_qs, urlparse

import pytest
from sqlalchemy import select

from app.crypto import decrypt_password, encrypt_password
from app.database import session_scope
from app.models import Account
from app.services import google_oauth, imap_client, smtp_service
from tests.helpers import create_account, csrf_token, login


@pytest.fixture()
def configured(monkeypatch):
    monkeypatch.setattr(google_oauth, "GOOGLE_OAUTH_CLIENT_ID", "cid.apps.googleusercontent.com")
    monkeypatch.setattr(google_oauth, "GOOGLE_OAUTH_CLIENT_SECRET", "csecret")
    google_oauth._cache.clear()
    yield
    google_oauth._cache.clear()


def _id_token(claims: dict) -> str:
    body = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    return f"header.{body}.signature"


def test_pkce_and_authorize_url(configured):
    verifier, challenge = google_oauth.new_pkce()
    assert challenge == base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    url = google_oauth.authorize_url("https://mail.example/accounts/oauth/callback", "st", challenge)
    qs = parse_qs(urlparse(url).query)
    assert qs["access_type"] == ["offline"] and qs["code_challenge_method"] == ["S256"]
    assert "https://mail.google.com/" in qs["scope"][0]


def test_exchange_code_validates_id_token(configured, monkeypatch):
    good = {"aud": "cid.apps.googleusercontent.com", "iss": "https://accounts.google.com",
            "email": "Me@Gmail.com", "email_verified": True}
    monkeypatch.setattr(google_oauth, "_post", lambda data: {
        "refresh_token": "rt", "access_token": "at", "expires_in": 3600,
        "scope": "openid https://mail.google.com/ email", "id_token": _id_token(good),
    })
    assert google_oauth.exchange_code("c", "https://x/cb", "v") == ("me@gmail.com", "rt")

    bad = dict(good, aud="someone-else")
    monkeypatch.setattr(google_oauth, "_post", lambda data: {
        "refresh_token": "rt", "scope": "https://mail.google.com/", "id_token": _id_token(bad),
    })
    with pytest.raises(google_oauth.OAuthError):
        google_oauth.exchange_code("c", "https://x/cb", "v")


def test_access_token_cached_and_refreshed(configured, monkeypatch):
    calls = []

    def fake_post(data):
        calls.append(data["grant_type"])
        return {"access_token": f"token-{len(calls)}", "expires_in": 3600}

    monkeypatch.setattr(google_oauth, "_post", fake_post)
    assert google_oauth.access_token("rt") == "token-1"
    assert google_oauth.access_token("rt") == "token-1"          # 缓存
    google_oauth._cache[google_oauth._cache_key("rt")] = ("old", 0)   # 已过期
    assert google_oauth.access_token("rt") == "token-2"
    assert calls == ["refresh_token", "refresh_token"]


def test_oauth_flow_creates_account(client, configured, monkeypatch, _no_background_network):
    token = login(client)
    resp = client.get("/accounts/oauth/start", follow_redirects=False)
    assert resp.status_code == 303
    target = urlparse(resp.headers["location"])
    assert target.netloc == "accounts.google.com"
    qs = parse_qs(target.query)
    assert qs["redirect_uri"] == ["http://testserver/accounts/oauth/callback"]

    seen = {}

    def fake_exchange(code, redirect_uri, verifier):
        seen.update(code=code, redirect_uri=redirect_uri, verifier=verifier)
        return "oauth-user@gmail.com", "refresh-abc"

    monkeypatch.setattr(google_oauth, "exchange_code", fake_exchange)
    resp = client.get("/accounts/oauth/callback", params={"code": "the-code", "state": qs["state"][0]},
                      follow_redirects=False)
    assert resp.status_code == 303
    assert seen["code"] == "the-code" and seen["verifier"]
    with session_scope() as db:
        acc = db.execute(select(Account).where(Account.email == "oauth-user@gmail.com")).scalar_one()
        assert decrypt_password(acc.encrypted_password) == "oauth2:refresh-abc"
    assert any(name == "sync_then_backfill" for name, *_ in _no_background_network)
    assert resp.headers["location"] == "/accounts/"
    data = client.get("/api/accounts").json()
    assert data["flash"] == {"message": "已通过 Google 授权添加 oauth-user@gmail.com", "level": "success"}
    assert next(a for a in data["accounts"] if a["email"] == "oauth-user@gmail.com")["oauth"] is True
    # 提示只显示一次
    assert client.get("/api/accounts").json()["flash"] is None


def test_callback_rejects_wrong_state(client, configured, monkeypatch):
    token = login(client)
    client.get("/accounts/oauth/start", follow_redirects=False)
    monkeypatch.setattr(google_oauth, "exchange_code", lambda *a: pytest.fail("must not exchange"))
    client.get("/accounts/oauth/callback", params={"code": "x", "state": "forged"}, follow_redirects=False)
    assert client.get("/api/accounts").json()["flash"]["message"] == "授权请求已失效"


def test_reauthorize_switches_existing_password_account(client, configured, monkeypatch):
    token = login(client)
    acc = create_account("switch@gmail.com")
    resp = client.get("/accounts/oauth/start", follow_redirects=False)
    state = parse_qs(urlparse(resp.headers["location"]).query)["state"][0]
    monkeypatch.setattr(google_oauth, "exchange_code", lambda *a: ("switch@gmail.com", "rt-switch"))
    client.get("/accounts/oauth/callback", params={"code": "c", "state": state}, follow_redirects=False)
    with session_scope() as db:
        assert decrypt_password(db.get(Account, acc).encrypted_password) == "oauth2:rt-switch"


def test_imap_uses_xoauth2_for_oauth_accounts(monkeypatch):
    calls = []

    class FakeIMAP:
        def __init__(self, **kwargs):
            pass

        def oauth2_login(self, user, token):
            calls.append(("oauth2", user, token))

        def login(self, user, password):
            calls.append(("login", user, password))

        def logout(self):
            pass

    monkeypatch.setattr(imap_client, "IMAPClient", FakeIMAP)
    monkeypatch.setattr(google_oauth, "access_token_for_secret", lambda secret: "access-xyz")
    with imap_client._imap_client("u@gmail.com", "oauth2:rt"):
        pass
    with imap_client._imap_client("u@gmail.com", "apppassword1234"):
        pass
    assert calls == [("oauth2", "u@gmail.com", "access-xyz"), ("login", "u@gmail.com", "apppassword1234")]


def test_imap_reports_revoked_authorization(monkeypatch):
    class FakeIMAP:
        def __init__(self, **kwargs):
            pass

        def logout(self):
            pass

    def revoked(secret):
        raise google_oauth.OAuthError("invalid_grant")

    monkeypatch.setattr(imap_client, "IMAPClient", FakeIMAP)
    monkeypatch.setattr(google_oauth, "access_token_for_secret", revoked)
    with pytest.raises(imap_client.IMAPSyncError, match="Google 授权已失效"):
        with imap_client._imap_client("u@gmail.com", "oauth2:rt"):
            pass


def test_smtp_uses_xoauth2(monkeypatch):
    seen = {}

    class FakeSMTP:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def ehlo(self):
            pass

        def auth(self, mechanism, authobject):
            seen["auth"] = (mechanism, authobject())

        def login(self, *a):
            pytest.fail("password login must not be used")

        def send_message(self, msg, from_addr, to_addrs):
            seen["to"] = to_addrs

    monkeypatch.setattr(smtp_service.smtplib, "SMTP_SSL", FakeSMTP)
    monkeypatch.setattr(google_oauth, "access_token_for_secret", lambda secret: "tok")
    smtp_service.send_email(
        from_email="me@gmail.com", encrypted_password=encrypt_password("oauth2:rt"), from_name=None,
        to="you@example.com", cc=None, bcc=None, subject="s", body_text="b", body_html=None,
        attachments=[], use_ssl=True,
    )
    assert seen["auth"] == ("XOAUTH2", "user=me@gmail.com\x01auth=Bearer tok\x01\x01")
    assert seen["to"] == ["you@example.com"]


def test_settings_reports_oauth_configured(client, configured):
    login(client)
    assert client.get("/api/accounts").json()["oauth_configured"] is True


def test_settings_reports_oauth_not_configured(client):
    login(client)
    assert client.get("/api/accounts").json()["oauth_configured"] is False


def test_oauth_start_requires_login(client, configured):
    resp = client.get("/accounts/oauth/start", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/login")
