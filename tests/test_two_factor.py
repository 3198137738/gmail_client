"""两步验证：TOTP 算法、登录第二步、防重放、计入失败封禁、设置页开启 / 关闭。"""
from __future__ import annotations

import base64

import pytest
from sqlalchemy import delete

import app.routes.auth as auth_routes
from app import totp
from app.config import WEB_AUTH_MAX_FAILURES
from app.database import session_scope
from app.models import LoginThrottle
from app.services import two_factor
from tests.helpers import api_post, csrf_token, login


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
    return api_post(client, "/api/login", csrf_token(client), {"password": password})


def _code_step(client, code):
    return api_post(client, "/api/login/2fa", csrf_token(client), {"code": code})


def _logged_in(client) -> bool:
    return client.get("/api/messages").status_code == 200


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
    assert resp.status_code == 200 and resp.json()["two_factor"] is True
    # 只过了密码还不算登录
    assert not _logged_in(client)
    assert client.get("/api/session").json()["two_factor_pending"] is True

    resp = _code_step(client, totp.code_at(secret, totp.current_step()))
    assert resp.status_code == 200 and resp.json()["ok"] is True
    assert _logged_in(client)


def test_code_cannot_be_reused(client, secret):
    code = totp.code_at(secret, totp.current_step())
    _password_step(client)
    assert _code_step(client, code).status_code == 200
    api_post(client, "/api/logout", csrf_token(client))
    _password_step(client)
    resp = _code_step(client, code)
    assert resp.status_code == 400   # 同一个验证码第二次无效
    assert not _logged_in(client)


def _wrong(secret):
    return "000000" if totp.code_at(secret, totp.current_step()) != "000000" else "111111"


def test_wrong_codes_block_ip_and_survive_new_password_step(client, secret):
    """验证码输错计入该 IP 的失败次数（服务端计数）：重新输入密码不能清零，达到上限即封禁。"""
    import app.auth as auth_module

    for i in range(WEB_AUTH_MAX_FAILURES - 1):
        _password_step(client)                    # 每次都重新走密码步骤（相当于拿到新的 cookie）
        resp = _code_step(client, _wrong(secret))
        assert resp.status_code == 400 and "还可尝试" in resp.json()["detail"]
    _password_step(client)
    resp = _code_step(client, _wrong(secret))
    assert resp.status_code == 429 and resp.json()["expired"] is True   # 退回登录页
    assert auth_module.get_login_throttle_status("198.51.100.99").is_blocked

    # 已封禁：密码和验证码都正确也不能登录
    resp = _password_step(client)
    assert resp.status_code == 429
    assert not _logged_in(client)


def test_replayed_cookie_does_not_reset_code_attempts(client, secret):
    import app.auth as auth_module

    _password_step(client)
    pending_cookie = client.cookies.get("gmail_client_session")
    for _ in range(WEB_AUTH_MAX_FAILURES):
        client.cookies.set("gmail_client_session", pending_cookie, domain="testserver.local")  # 重放刚过密码时的 cookie
        _code_step(client, _wrong(secret))
    assert auth_module.get_login_throttle_status("198.51.100.99").is_blocked


def test_correct_code_clears_failures(client, secret):
    import app.auth as auth_module

    _password_step(client)
    _code_step(client, _wrong(secret))
    resp = _code_step(client, totp.code_at(secret, totp.current_step()))
    assert resp.status_code == 200
    assert auth_module.get_login_throttle_status("198.51.100.99").failed_attempts == 0


def test_2fa_without_password_step_rejected(client, secret):
    assert client.get("/api/session").json()["two_factor_pending"] is False
    resp = _code_step(client, totp.code_at(secret, totp.current_step()))
    assert resp.status_code == 400 and resp.json()["expired"] is True
    assert not _logged_in(client)


def test_setup_enable_and_disable_from_settings(client, monkeypatch):
    monkeypatch.setattr(auth_routes, "get_client_ip", lambda request: "198.51.100.98")
    token = login(client)
    try:
        assert api_post(client, "/api/security/2fa/setup", token).status_code == 200
        info = client.get("/api/security").json()["two_factor"]
        key = info["setup_secret"].replace(" ", "")
        assert info["setup_uri"].startswith("otpauth://totp/")

        bad = api_post(client, "/api/security/2fa/enable", token, {"code": "000000"})
        if totp.code_at(key, totp.current_step()) != "000000":
            assert bad.status_code == 400 and not two_factor.is_enabled()

        resp = api_post(client, "/api/security/2fa/enable", token, {"code": totp.code_at(key, totp.current_step())})
        assert resp.status_code == 200
        assert two_factor.is_enabled()
        assert client.get("/api/security").json()["two_factor"]["enabled"] is True

        # 关闭需要当前验证码（上一个时间步已用过，用下一个窗口的码）；输错计入该 IP 的失败次数
        api_post(client, "/api/security/2fa/disable", token, {"code": "000000"})
        import app.auth as auth_module
        assert auth_module.get_login_throttle_status("unknown").failed_attempts in (0, 1)
        assert two_factor.is_enabled() or totp.code_at(key, totp.current_step() + 1) == "000000"
        api_post(client, "/api/security/2fa/disable", token, {"code": totp.code_at(key, totp.current_step() + 1)})
        assert not two_factor.is_enabled()
    finally:
        two_factor.disable()


def test_emergency_reset_ignores_secret(monkeypatch, secret):
    monkeypatch.setattr(two_factor, "WEB_AUTH_2FA_RESET", True)
    assert not two_factor.is_enabled()


def test_disable_is_throttled_per_ip(client, monkeypatch):
    """拿到未退出会话的人不能靠反复猜验证码关掉两步验证。"""
    import app.auth as auth_module

    token = login(client)
    key = totp.generate_secret()
    two_factor.enable(key)
    try:
        wrong = "000000" if totp.code_at(key, totp.current_step()) != "000000" else "111111"
        for _ in range(WEB_AUTH_MAX_FAILURES):
            api_post(client, "/api/security/2fa/disable", token, {"code": wrong})
        assert auth_module.get_login_throttle_status("unknown").is_blocked
        # 封禁期间正确的验证码也不能关闭
        resp = api_post(client, "/api/security/2fa/disable", token, {"code": totp.code_at(key, totp.current_step())})
        assert resp.status_code == 429
        assert two_factor.is_enabled()
    finally:
        two_factor.disable()
        with session_scope() as db:
            db.execute(delete(LoginThrottle))
