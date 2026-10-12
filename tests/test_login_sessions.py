"""登录设备管理：服务端会话、登出其他设备、改密码后旧会话失效；审计日志与新 IP 告警。"""
from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.database import session_scope
from app.models import AppSetting, AuditLog, LoginSession
from app.services import audit, login_sessions
from tests.helpers import api_post, csrf_token, login


def _new_client(app) -> TestClient:
    return TestClient(app, base_url="http://testserver")


def _events() -> list[str]:
    with session_scope() as db:
        return [e for e in db.execute(select(AuditLog.event).order_by(AuditLog.id)).scalars()]


def _logged_in(c) -> bool:
    return c.get("/api/messages").status_code == 200


def test_login_creates_session_and_audit(client):
    before = len(_events())
    login(client)
    assert _logged_in(client)
    assert "login_success" in _events()[before:]
    sessions = client.get("/api/security").json()["sessions"]
    assert sum(1 for s in sessions if s["is_current"]) == 1


def test_failed_login_is_audited(client):
    token = csrf_token(client)
    resp = api_post(client, "/api/login", token, {"password": "wrong"})
    assert resp.status_code == 400
    assert _events()[-1] == "login_failed"
    from app.auth import clear_failed_logins

    clear_failed_logins("unknown")


def test_revoke_others_keeps_current(app):
    with _new_client(app) as a, _new_client(app) as b:
        login(a)
        token_b = login(b)
        resp = api_post(b, "/api/security/sessions/revoke-others", token_b)
        assert resp.status_code == 200
        assert not _logged_in(a)
        assert _logged_in(b)


def test_revoke_all_logs_out_everyone(app):
    with _new_client(app) as a, _new_client(app) as b:
        login(a)
        token_b = login(b)
        resp = api_post(b, "/api/security/sessions/revoke-all", token_b)
        assert resp.json()["logged_out"] is True
        assert not _logged_in(a)
        assert not _logged_in(b)


def _newest_session_id() -> int:
    with session_scope() as db:
        return max(db.execute(select(LoginSession.id)).scalars())


def test_revoke_single_device(app):
    with _new_client(app) as a, _new_client(app) as b:
        login(a)
        session_a = _newest_session_id()
        token_b = login(b)
        resp = api_post(b, f"/api/security/sessions/{session_a}/revoke", token_b)
        assert resp.json()["logged_out"] is False
        assert not _logged_in(a)
        assert _logged_in(b)


def test_revoke_current_device_logs_out(client):
    token = login(client)
    current = next(s for s in client.get("/api/security").json()["sessions"] if s["is_current"])
    resp = api_post(client, f"/api/security/sessions/{current['id']}/revoke", token)
    assert resp.json()["logged_out"] is True
    assert not _logged_in(client)


def test_logout_revokes_server_session(client):
    token = login(client)
    active_before = len(login_sessions.list_active())
    api_post(client, "/api/logout", token)
    assert len(login_sessions.list_active()) == active_before - 1
    assert not _logged_in(client)


def test_password_change_revokes_all_sessions(app, monkeypatch):
    alerts: list[str] = []
    monkeypatch.setattr(audit, "send_alert", alerts.append)
    with session_scope() as db:
        db.execute(delete(AppSetting).where(AppSetting.key == login_sessions.PASSWORD_FP_KEY))

    # 首次只记录指纹（应用启动时也会用当前密码 test-password 检查一次）
    assert login_sessions.check_password_change("test-password") is False
    with _new_client(app) as a:
        login(a)
        assert login_sessions.check_password_change("test-password") is False
        assert _logged_in(a)
        assert login_sessions.check_password_change("new-password") is True
        assert not _logged_in(a)
        assert _events()[-1] == "password_changed"
        assert alerts and "访问密码已修改" in alerts[0]
        # 指纹不能是明文
        with session_scope() as db:
            assert "new-password" not in db.get(AppSetting, login_sessions.PASSWORD_FP_KEY).value
        login_sessions.check_password_change("test-password")   # 恢复，避免影响其他测试


def test_new_ip_alert_only_first_time(monkeypatch):
    alerts: list[str] = []
    monkeypatch.setattr(audit, "send_alert", alerts.append)
    ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130.0 Safari/537.36"
    audit.on_login_success("203.0.113.77", ua)
    audit.on_login_success("203.0.113.77", ua)
    assert len(alerts) == 1
    assert "203.0.113.77" in alerts[0] and "Chrome · Windows" in alerts[0]


def test_audit_page_lists_events(client):
    login(client)
    audit.record("mail_deleted", "「<script>x</script>」", ip="198.51.100.1")
    rows = client.get("/api/security/audit").json()["rows"]
    row = next(r for r in rows if r["ip_address"] == "198.51.100.1")
    assert row["event_label"] == "删除邮件"
    # 详情原样返回，由前端按文本显示（不作为 HTML 插入）
    assert row["detail"] == "「<script>x</script>」"


def test_describe_user_agent():
    assert login_sessions.describe_user_agent(
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Version/17.0 Mobile/15E148 Safari/604.1"
    ) == "Safari · iPhone"
    assert login_sessions.describe_user_agent(None) == "未知设备"
