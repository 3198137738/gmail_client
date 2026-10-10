"""登录会话：绝对有效期、旧会话失效。"""
from __future__ import annotations

import time

import app.auth as auth
from tests.helpers import csrf_token, login


def test_login_required(client):
    resp = client.get("/messages/", follow_redirects=False)
    assert resp.status_code == 303
    assert "/login" in resp.headers["location"]


def test_login_then_access(client):
    login(client)
    assert client.get("/messages/", follow_redirects=False).status_code == 200


def test_session_expires_after_absolute_max_age(client, monkeypatch):
    login(client)
    real_time = time.time
    monkeypatch.setattr(auth.time, "time", lambda: real_time() + auth.SESSION_MAX_AGE_SEC + 60)
    resp = client.get("/messages/", follow_redirects=False)
    assert resp.status_code == 303
    assert "/login" in resp.headers["location"]


def test_cookie_flags(client):
    token = csrf_token(client, "/login")
    resp = client.post(
        "/login", data={"password": "test-password", "csrf_token": token}, follow_redirects=False
    )
    cookie = resp.headers["set-cookie"].lower()
    assert "httponly" in cookie
    assert "samesite=lax" in cookie
    assert "max-age=" in cookie
