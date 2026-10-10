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
        "/login",
        data={"password": password, "csrf_token": csrf_token(client)},
        follow_redirects=False,
    )


def test_ip_blocked_after_max_failures(client, fake_ip):
    for _ in range(WEB_AUTH_MAX_FAILURES):
        resp = _attempt(client, "wrong")
        assert "/login" in resp.headers["location"]

    # 已封禁：正确密码也不行
    resp = _attempt(client, "test-password")
    assert "/login" in resp.headers["location"]
    assert client.get("/messages/", follow_redirects=False).status_code == 303

    # 其他 IP 不受影响
    fake_ip["ip"] = "198.51.100.78"
    resp = _attempt(client, "test-password")
    assert resp.headers["location"].endswith("/messages/")


def test_success_resets_failure_counter(client, fake_ip):
    for _ in range(WEB_AUTH_MAX_FAILURES - 1):
        _attempt(client, "wrong")
    resp = _attempt(client, "test-password")
    assert resp.headers["location"].endswith("/messages/")
    with session_scope() as db:
        row = db.query(LoginThrottle).filter_by(ip_address="198.51.100.77").one()
        assert row.failed_attempts == 0
