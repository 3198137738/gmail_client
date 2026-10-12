"""登录失败封禁：连续输错达到上限后，该 IP 即使密码正确也无法登录。"""
from __future__ import annotations

import pytest
from sqlalchemy import delete

import app.routes.auth as auth_routes
from app.config import WEB_AUTH_MAX_FAILURES
from app.database import session_scope
from app.models import LoginThrottle
from tests.helpers import csrf_token


@pytest.fixture()
def fake_ip(monkeypatch):
    state = {"ip": "198.51.100.77"}
    monkeypatch.setattr(auth_routes, "get_client_ip", lambda request: state["ip"])
    yield state
    with session_scope() as db:
        db.execute(delete(LoginThrottle))


def _attempt(client, password: str):
    return client.post(
        "/api/login",
        json={"password": password},
        headers={"X-CSRF-Token": csrf_token(client)},
    )


def _ok(resp) -> bool:
    return resp.status_code == 200 and resp.json()["ok"] is True


def test_ip_blocked_after_max_failures(client, fake_ip):
    for i in range(WEB_AUTH_MAX_FAILURES):
        resp = _attempt(client, "wrong")
        assert not _ok(resp)
    assert resp.status_code == 429 and resp.json()["blocked"] is True

    # 已封禁：正确密码也不行
    resp = _attempt(client, "test-password")
    assert resp.status_code == 429
    assert client.get("/api/messages").status_code == 401
    assert client.get("/api/session").json()["blocked"] is True

    # 其他 IP 不受影响
    fake_ip["ip"] = "198.51.100.78"
    assert _ok(_attempt(client, "test-password"))


def test_success_resets_failure_counter(client, fake_ip):
    for _ in range(WEB_AUTH_MAX_FAILURES - 1):
        _attempt(client, "wrong")
    assert _ok(_attempt(client, "test-password"))
    with session_scope() as db:
        row = db.query(LoginThrottle).filter_by(ip_address="198.51.100.77").one()
        assert row.failed_attempts == 0


def test_ipv6_failures_count_per_64(client, fake_ip):
    import app.auth as web_auth

    # 同一 /64 内轮换地址不能绕过封禁
    for i in range(WEB_AUTH_MAX_FAILURES):
        fake_ip["ip"] = f"2001:db8:1:2::{i + 1:x}"
        _attempt(client, "wrong")
    fake_ip["ip"] = "2001:db8:1:2:abcd::99"
    assert _attempt(client, "test-password").status_code == 429
    assert web_auth.get_login_throttle_status("2001:db8:1:2::ffff").is_blocked

    # 别的 /64 不受影响
    fake_ip["ip"] = "2001:db8:1:3::1"
    assert _ok(_attempt(client, "test-password"))


def test_ipv6_auto_ban_covers_whole_64(monkeypatch):
    import app.auth as web_auth

    monkeypatch.setattr(web_auth, "WEB_AUTH_PERMANENT_BLOCK_AFTER_FAILURES", True)
    try:
        for _ in range(WEB_AUTH_MAX_FAILURES):
            web_auth.register_failed_login("2001:db8:9:9::1")
        assert web_auth.is_ip_banned("2001:db8:9:9:1234::5")
        assert not web_auth.is_ip_banned("2001:db8:9:a::1")
        assert web_auth.normalize_ip("2001:db8:9:9::/64") == "2001:db8:9:9::/64"
        assert web_auth.unban_ip("2001:db8:9:9::/64")
        assert not web_auth.is_ip_banned("2001:db8:9:9:1234::5")
    finally:
        with session_scope() as db:
            db.execute(delete(LoginThrottle))
        web_auth.invalidate_ban_cache()


def test_global_failure_limit_only_admits_known_networks(client, fake_ip, monkeypatch):
    import app.auth as web_auth
    from app.models import AuditLog

    with session_scope() as db:
        db.execute(delete(AuditLog))
    fake_ip["ip"] = "198.51.100.200"
    assert _ok(_attempt(client, "test-password"))  # 已知 IP

    monkeypatch.setattr(web_auth, "WEB_AUTH_GLOBAL_MAX_FAILURES", 4)
    # 攻击者轮换 IP：每个 IP 都没到单 IP 上限，但合计达到全局上限
    for i in range(4):
        fake_ip["ip"] = f"203.0.113.{i + 1}"
        _attempt(client, "wrong")

    fake_ip["ip"] = "203.0.113.50"          # 新 IP：密码正确也暂时不能登录
    resp = _attempt(client, "test-password")
    assert resp.status_code == 429 and "以前登录过的网络" in resp.json()["detail"]

    fake_ip["ip"] = "198.51.100.200"        # 以前登录成功过的 IP 不受影响
    assert _ok(_attempt(client, "test-password"))
