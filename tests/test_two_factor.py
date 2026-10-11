"""两步验证：TOTP 算法、登录第二步、防重放、计入失败封禁、设置页开启 / 关闭。"""
from __future__ import annotations

import base64
import re

import pytest
from sqlalchemy import delete

import app.routes.auth as auth_routes
from app import totp
from app.config import WEB_AUTH_MAX_FAILURES
from app.database import session_scope
from app.models import LoginThrottle
from app.services import two_factor
from tests.helpers import csrf_token, login


@pytest.fixture()
def secret(monkeypatch):
    """开启两步验证（测试结束后一定关闭，避免影响其他测试的登录）。"""
    monkeypatch.setattr(auth_routes, "get_client_ip", lambda request: "198.51.100.99")
    value = totp.generate_secret()
    two_factor.enable(value)
    yield value
    two_factor.disable()
    with session_scope() as db:
        db.execute(delete(LoginThrottle))


def _password_step(client, password="test-password"):
    return client.post(
        "/login",
        data={"password": password, "next": "/messages/", "csrf_token": csrf_token(client)},
        follow_redirects=False,
    )


def _code_step(client, code):
    token = csrf_token(client, "/login/2fa")
    return client.post("/login/2fa", data={"code": code, "csrf_token": token}, follow_redirects=False)


def test_rfc6238_vectors():
    key = base64.b32encode(b"12345678901234567890").decode()
    assert totp.code_at(key, 59 // 30, digits=8) == "94287082"
    assert totp.code_at(key, 1111111109 // 30, digits=8) == "07081804"
    assert totp.code_at(key, 20000000000 // 30, digits=8) == "65353130"


def test_verify_window_and_replay():
    key = totp.generate_secret()
    now = 1_700_000_000
    step = totp.current_step(now)
    assert totp.verify(key, totp.code_at(key, step), now=now) == step
    assert totp.verify(key, totp.code_at(key, step - 1), now=now) == step - 1      # 允许 30 秒误差
    assert totp.verify(key, totp.code_at(key, step - 3), now=now) is None
    assert totp.verify(key, totp.code_at(key, step), now=now, last_step=step) is None  # 防重放
    assert totp.verify(key, "12ab56", now=now) is None


def test_login_requires_code_when_enabled(client, secret):
    resp = _password_step(client)
    assert resp.status_code == 303 and resp.headers["location"].endswith("/login/2fa")
    # 只过了密码还不算登录
    assert client.get("/messages/", follow_redirects=False).status_code == 303

    resp = _code_step(client, totp.code_at(secret, totp.current_step()))
    assert resp.status_code == 303 and resp.headers["location"] == "/messages/"
    assert client.get("/messages/", follow_redirects=False).status_code == 200


def test_code_cannot_be_reused(client, secret):
    code = totp.code_at(secret, totp.current_step())
    _password_step(client)
    assert _code_step(client, code).headers["location"] == "/messages/"
    client.post("/logout", data={"csrf_token": csrf_token(client, "/messages/")})
    _password_step(client)
    resp = _code_step(client, code)
    assert resp.headers["location"].endswith("/login/2fa")   # 同一个验证码第二次无效


def _wrong(secret):
    return "000000" if totp.code_at(secret, totp.current_step()) != "000000" else "111111"


def test_wrong_codes_do_not_block_ip(client, secret):
    """验证码输错不计入失败次数、不封禁 IP。"""
    import app.auth as auth_module

    for _ in range(WEB_AUTH_MAX_FAILURES + 2):
        _password_step(client)
        _code_step(client, _wrong(secret))
    assert not auth_module.get_login_throttle_status("198.51.100.99").is_blocked
    # 之后输入正确的验证码仍能登录
    _password_step(client)
    resp = _code_step(client, totp.code_at(secret, totp.current_step()))
    assert resp.headers["location"] == "/messages/"


def test_too_many_wrong_codes_return_to_password_step(client, secret):
    _password_step(client)
    for _ in range(auth_routes.TWO_FACTOR_MAX_ATTEMPTS - 1):
        assert _code_step(client, _wrong(secret)).headers["location"].endswith("/login/2fa")
    resp = _code_step(client, _wrong(secret))
    assert "/login/2fa" not in resp.headers["location"]          # 退回密码步骤
    assert client.get("/login/2fa", follow_redirects=False).headers["location"].endswith("/login")
    import app.auth as auth_module
    assert not auth_module.get_login_throttle_status("198.51.100.99").is_blocked


def test_2fa_page_without_password_step_redirects(client, secret):
    resp = client.get("/login/2fa", follow_redirects=False)
    assert resp.status_code == 303 and resp.headers["location"].endswith("/login")


def test_setup_enable_and_disable_from_settings(client, monkeypatch):
    monkeypatch.setattr(auth_routes, "get_client_ip", lambda request: "198.51.100.98")
    token = login(client)
    try:
        client.post("/accounts/security/setup", data={"csrf_token": token})
        page = client.get("/accounts/security").text
        shown = re.search(r'<code id="gm-2fa-secret">([A-Z2-7 ]+)</code>', page).group(1)
        key = shown.replace(" ", "")
        assert "otpauth://totp/" in page

        bad = client.post("/accounts/security/enable", data={"code": "000000", "csrf_token": token})
        assert not two_factor.is_enabled() or totp.code_at(key, totp.current_step()) == "000000"
        assert bad.status_code == 200

        client.post("/accounts/security/enable",
                    data={"code": totp.code_at(key, totp.current_step()), "csrf_token": token})
        assert two_factor.is_enabled()
        assert "已开启" in client.get("/accounts/security").text

        # 关闭需要当前验证码（上一个时间步已用过，用下一个窗口的码）
        client.post("/accounts/security/disable", data={"code": "000000", "csrf_token": token})
        assert two_factor.is_enabled() or totp.code_at(key, totp.current_step() + 1) == "000000"
        client.post("/accounts/security/disable",
                    data={"code": totp.code_at(key, totp.current_step() + 1), "csrf_token": token})
        assert not two_factor.is_enabled()
    finally:
        two_factor.disable()


def test_emergency_reset_ignores_secret(monkeypatch, secret):
    monkeypatch.setattr(two_factor, "WEB_AUTH_2FA_RESET", True)
    assert not two_factor.is_enabled()
