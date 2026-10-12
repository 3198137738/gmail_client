"""一键永久拉黑 IP：拉黑后该 IP 所有请求返回 403，已登录设备登出；可在设置里解除。"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

import app.auth as web_auth
import app.routes.auth as auth_routes
from app.database import session_scope
from app.models import AuditLog, LoginThrottle
from tests.helpers import api_post, csrf_token, login

ADMIN_IP = "198.51.100.10"
BAD_IP = "203.0.113.5"


@pytest.fixture()
def fake_ips(monkeypatch):
    """用请求头 X-Test-IP 指定客户端 IP，方便模拟多台设备。"""
    def fake(request):
        return request.headers.get("x-test-ip", "unknown")

    monkeypatch.setattr(web_auth, "get_client_ip", fake)
    monkeypatch.setattr(auth_routes, "get_client_ip", fake)
    yield
    with session_scope() as db:
        db.execute(delete(LoginThrottle))
    web_auth.invalidate_ban_cache()


def _client(app, ip: str) -> TestClient:
    return TestClient(app, base_url="http://testserver", headers={"X-Test-IP": ip})


def _last_event() -> tuple[str, str | None]:
    with session_scope() as db:
        row = db.execute(select(AuditLog).order_by(AuditLog.id.desc()).limit(1)).scalar_one()
        return row.event, row.detail


def _logged_in(c) -> bool:
    return c.get("/api/messages").status_code == 200


def test_ban_blocks_all_requests_and_revokes_sessions(app, fake_ips):
    with _client(app, ADMIN_IP) as admin, _client(app, BAD_IP) as bad:
        token = login(admin)
        login(bad)
        assert _logged_in(bad)

        resp = api_post(admin, "/api/security/ip-ban", token, {"ip": BAD_IP})
        assert resp.status_code == 200 and "已永久拉黑" in resp.json()["message"]
        assert _last_event() == ("ip_banned", BAD_IP)
        assert web_auth.is_ip_banned(BAD_IP)

        # 被拉黑的 IP 什么都打不开（接口、页面、登录），健康检查不受影响
        assert bad.get("/api/messages").status_code == 403
        assert bad.get("/messages/", follow_redirects=False).status_code == 403
        assert bad.get("/login").status_code == 403
        assert bad.get("/api/session").status_code == 403
        assert bad.get("/healthz").status_code == 200

        banned = admin.get("/api/security").json()["banned_ips"]
        assert any(b["ip_address"] == BAD_IP and not b["from_env"] for b in banned)

        # 再拉黑一次提示已在黑名单中
        resp = api_post(admin, "/api/security/ip-ban", token, {"ip": BAD_IP})
        assert resp.status_code == 400 and "已在黑名单中" in resp.json()["detail"]

        resp = api_post(admin, "/api/security/ip-unban", token, {"ip": BAD_IP})
        assert resp.status_code == 200
        assert _last_event() == ("ip_unbanned", BAD_IP)
        assert not web_auth.is_ip_banned(BAD_IP)
        # 解封后能访问登录接口，但之前的会话已被撤销，需要重新登录
        assert bad.get("/api/session").status_code == 200
        assert not _logged_in(bad)


def test_cannot_ban_own_or_invalid_ip(app, fake_ips):
    with _client(app, ADMIN_IP) as admin:
        token = login(admin)
        resp = api_post(admin, "/api/security/ip-ban", token, {"ip": ADMIN_IP})
        assert resp.status_code == 400 and "不能拉黑自己当前的 IP" in resp.json()["detail"]
        resp = api_post(admin, "/api/security/ip-ban", token, {"ip": "not-an-ip"})
        assert resp.status_code == 400 and "IP 地址格式不正确" in resp.json()["detail"]
        assert not web_auth.is_ip_banned(ADMIN_IP)
        assert _logged_in(admin)


def test_ban_from_audit_log(app, fake_ips):
    with _client(app, ADMIN_IP) as admin, _client(app, BAD_IP) as bad:
        token = login(admin)
        api_post(bad, "/api/login", csrf_token(bad), {"password": "wrong"})
        rows = admin.get("/api/security/audit").json()["rows"]
        row = next(r for r in rows if r["ip_address"] == BAD_IP)
        assert row["ip_banned"] is False

        assert api_post(admin, "/api/security/ip-ban", token, {"ip": BAD_IP}).status_code == 200
        data = admin.get("/api/security/audit").json()
        assert next(r for r in data["rows"] if r["ip_address"] == BAD_IP)["ip_banned"] is True
        assert data["current_ip"] == ADMIN_IP


def test_auto_permanent_block_also_denies_all_requests(app, fake_ips, monkeypatch):
    monkeypatch.setattr(web_auth, "WEB_AUTH_PERMANENT_BLOCK_AFTER_FAILURES", True)
    assert not web_auth.is_ip_banned(BAD_IP)  # 先把缓存填上，确认封禁后会刷新
    for _ in range(web_auth.WEB_AUTH_MAX_FAILURES):
        web_auth.register_failed_login(BAD_IP)
    with _client(app, BAD_IP) as bad:
        assert bad.get("/login").status_code == 403
