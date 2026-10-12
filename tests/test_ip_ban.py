"""一键永久拉黑 IP：拉黑后该 IP 所有请求返回 403，已登录设备登出；可在设置里解除。"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

import app.auth as web_auth
import app.routes.auth as auth_routes
from app.database import session_scope
from app.models import AuditLog, LoginThrottle
from tests.helpers import csrf_token, login

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


def test_ban_blocks_all_requests_and_revokes_sessions(app, fake_ips):
    with _client(app, ADMIN_IP) as admin, _client(app, BAD_IP) as bad:
        token = login(admin)
        login(bad)
        assert bad.get("/messages/", follow_redirects=False).status_code == 200

        resp = admin.post("/accounts/security/ip-ban", data={"ip": BAD_IP, "csrf_token": token},
                          follow_redirects=False)
        assert resp.status_code == 303
        assert _last_event() == ("ip_banned", BAD_IP)
        assert web_auth.is_ip_banned(BAD_IP)

        # 被拉黑的 IP 连登录页也打不开，健康检查不受影响
        assert bad.get("/messages/", follow_redirects=False).status_code == 403
        assert bad.get("/login").status_code == 403
        assert bad.get("/healthz").status_code == 200

        page = admin.get("/accounts/security").text
        assert "IP 黑名单" in page and BAD_IP in page and "已永久拉黑" in page

        # 再拉黑一次提示已在黑名单中
        page = admin.post("/accounts/security/ip-ban", data={"ip": BAD_IP, "csrf_token": token}).text
        assert "已在黑名单中" in page

        resp = admin.post("/accounts/security/ip-unban", data={"ip": BAD_IP, "csrf_token": token},
                          follow_redirects=False)
        assert resp.status_code == 303
        assert _last_event() == ("ip_unbanned", BAD_IP)
        assert not web_auth.is_ip_banned(BAD_IP)
        # 解封后能访问登录页，但之前的会话已被撤销，需要重新登录
        assert bad.get("/login").status_code == 200
        assert bad.get("/messages/", follow_redirects=False).status_code == 303


def test_cannot_ban_own_or_invalid_ip(app, fake_ips):
    with _client(app, ADMIN_IP) as admin:
        token = login(admin)
        page = admin.post("/accounts/security/ip-ban", data={"ip": ADMIN_IP, "csrf_token": token}).text
        assert "不能拉黑自己当前的 IP" in page
        page = admin.post("/accounts/security/ip-ban", data={"ip": "not-an-ip", "csrf_token": token}).text
        assert "IP 地址格式不正确" in page
        assert not web_auth.is_ip_banned(ADMIN_IP)
        assert admin.get("/messages/", follow_redirects=False).status_code == 200


def test_ban_from_audit_log_returns_to_same_page(app, fake_ips):
    with _client(app, ADMIN_IP) as admin, _client(app, BAD_IP) as bad:
        token = login(admin)
        bad.post("/login", data={"password": "wrong", "csrf_token": csrf_token(bad)})
        page = admin.get("/accounts/security/audit").text
        assert f'name="ip" value="{BAD_IP}"' in page

        resp = admin.post("/accounts/security/ip-ban",
                          data={"ip": BAD_IP, "audit_page": "1", "csrf_token": token},
                          follow_redirects=False)
        assert resp.headers["location"].endswith("/accounts/security/audit?page=1")
        page = admin.get("/accounts/security/audit").text
        assert "已永久拉黑" in page and "已拉黑" in page


def test_auto_permanent_block_also_denies_all_requests(app, fake_ips, monkeypatch):
    monkeypatch.setattr(web_auth, "WEB_AUTH_PERMANENT_BLOCK_AFTER_FAILURES", True)
    assert not web_auth.is_ip_banned(BAD_IP)  # 先把缓存填上，确认封禁后会刷新
    for _ in range(web_auth.WEB_AUTH_MAX_FAILURES):
        web_auth.register_failed_login(BAD_IP)
    with _client(app, BAD_IP) as bad:
        assert bad.get("/login").status_code == 403
