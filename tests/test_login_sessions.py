"""登录设备管理：服务端会话、登出其他设备、改密码后旧会话失效；审计日志与新 IP 告警。"""
from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.database import session_scope
from app.models import AppSetting, AuditLog, LoginSession
from app.services import audit, login_sessions
from tests.helpers import csrf_token, login


def _new_client(app) -> TestClient:
    return TestClient(app, base_url="http://testserver")


def _events() -> list[str]:
    with session_scope() as db:
        return [e for e in db.execute(select(AuditLog.event).order_by(AuditLog.id)).scalars()]


def _logged_in(c) -> bool:
    return c.get("/messages/", follow_redirects=False).status_code == 200


def test_login_creates_session_and_audit(client):
    before = len(_events())
    login(client)
    assert _logged_in(client)
    assert "login_success" in _events()[before:]
    page = client.get("/accounts/security").text
    assert "登录设备" in page and "本机" in page


def test_failed_login_is_audited(client):
    token = csrf_token(client, "/login")
    client.post("/login", data={"password": "wrong", "csrf_token": token}, follow_redirects=False)
    assert _events()[-1] == "login_failed"
    from app.auth import clear_failed_logins

    clear_failed_logins("unknown")


def test_revoke_others_keeps_current(app):
    with _new_client(app) as a, _new_client(app) as b:
        login(a)
        token_b = login(b)
        resp = b.post("/accounts/security/sessions/revoke-others", data={"csrf_token": token_b},
                      follow_redirects=False)
        assert resp.status_code == 303
        assert not _logged_in(a)
        assert _logged_in(b)


def test_revoke_all_logs_out_everyone(app):
    with _new_client(app) as a, _new_client(app) as b:
        login(a)
        token_b = login(b)
        resp = b.post("/accounts/security/sessions/revoke-all", data={"csrf_token": token_b},
                      follow_redirects=False)
        assert "/login" in resp.headers["location"]
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
        b.post(f"/accounts/security/sessions/{session_a}/revoke", data={"csrf_token": token_b})
        assert not _logged_in(a)
        assert _logged_in(b)


def test_logout_revokes_server_session(client):
    token = login(client)
    active_before = len(login_sessions.list_active())
    client.post("/logout", data={"csrf_token": token}, follow_redirects=False)
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
    page = client.get("/accounts/security/audit").text
    assert "删除邮件" in page and "198.51.100.1" in page
    assert "<script>x</script>" not in page   # 详情被转义


def test_describe_user_agent():
    assert login_sessions.describe_user_agent(
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Version/17.0 Mobile/15E148 Safari/604.1"
    ) == "Safari · iPhone"
    assert login_sessions.describe_user_agent(None) == "未知设备"
