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


def test_password_with_non_ascii_characters(client, monkeypatch):
    """含中文的密码：之前 compare_digest(str, str) 抛 TypeError，登录页 500。"""
    import app.routes.auth as auth_routes

    assert auth.password_matches("邮箱密码123", "邮箱密码123")
    assert not auth.password_matches("邮箱密码124", "邮箱密码123")
    assert not auth.password_matches("abc", "邮箱密码123")
    monkeypatch.setattr(auth_routes, "WEB_AUTH_PASSWORD", "邮箱密码123")
    login(client, password="邮箱密码123")


def test_refuses_to_start_without_password():
    import pytest

    from app.main import check_auth_configured

    with pytest.raises(RuntimeError, match="WEB_AUTH_PASSWORD"):
        check_auth_configured(enabled=False, allow_no_auth=False)
    check_auth_configured(enabled=False, allow_no_auth=True)   # 显式允许
    check_auth_configured(enabled=True, allow_no_auth=False)
