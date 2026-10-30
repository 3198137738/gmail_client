"""HTTPS 证书检测与续订：读取真实 TLS 服务器的证书、到期 / 无效时提醒（每天一次）、
通过文件与宿主机续订脚本交换请求和结果。"""
from __future__ import annotations

import os
import socket
import ssl
import threading
from datetime import datetime, timedelta, timezone

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from app.database import session_scope
from app.models import AppSetting, AuditLog
from app.services import audit, cert_monitor
from tests.helpers import api_post, login

HOST = "mail.example.test"


@pytest.fixture(autouse=True)
def clean_state(tmp_path, monkeypatch):
    with session_scope() as db:
        for key in (cert_monitor.STATUS_KEY, cert_monitor.DOMAIN_KEY, cert_monitor.RESULT_SEEN_KEY):
            row = db.get(AppSetting, key)
            if row is not None:
                db.delete(row)
    monkeypatch.setattr(cert_monitor, "CERT_DIR", tmp_path / "cert")
    monkeypatch.setattr(cert_monitor, "CERT_DOMAIN", "")
    alerts = []
    monkeypatch.setattr(audit, "send_alert", alerts.append)
    return alerts


def _self_signed(tmp_path, days: int):
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, HOST), x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Test CA")])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=days))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(HOST)]), critical=False)
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = tmp_path / "cert.pem", tmp_path / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ))
    return cert_path, key_path


def _serve_tls(cert_path, key_path):
    """本机 TLS 服务器：接受连接、完成握手后关闭。返回端口。"""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert_path, key_path)
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen()

    def loop():
        while True:
            try:
                conn, _ = server.accept()
            except OSError:
                return
            try:
                with ctx.wrap_socket(conn, server_side=True):
                    pass
            except (OSError, ssl.SSLError):
                pass

    threading.Thread(target=loop, daemon=True).start()
    return server


def test_fetch_certificate_from_tls_server(tmp_path, monkeypatch):
    cert_path, key_path = _self_signed(tmp_path, days=40)
    server = _serve_tls(cert_path, key_path)
    port = server.getsockname()[1]
    try:
        # 自签名证书：校验失败，但仍读出有效期等信息
        info = cert_monitor.fetch_certificate(HOST, address="127.0.0.1", port=port)
        assert info["verify_error"] and info["names"] == [HOST] and "Test CA" in info["issuer"]
        assert 39 <= (info["not_after"] - datetime.now(timezone.utc)).days <= 40

        # 信任这张证书后校验通过
        real = ssl.create_default_context
        monkeypatch.setattr(cert_monitor.ssl, "create_default_context", lambda: real(cafile=str(cert_path)))
        assert cert_monitor.fetch_certificate(HOST, address="127.0.0.1", port=port)["verify_error"] is None
    finally:
        server.close()


def _fake_cert(monkeypatch, days, verify_error=None):
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(cert_monitor, "fetch_certificate", lambda host, **kw: {
        "not_before": now - timedelta(days=60), "not_after": now + timedelta(days=days, hours=1),
        "issuer": "Let's Encrypt R11", "names": [host], "verify_error": verify_error,
    })


def test_levels_and_daily_alert(monkeypatch, clean_state):
    alerts = clean_state
    status = cert_monitor.check_now()
    assert status["level"] == "error" and "CERT_DOMAIN" in status["error"]

    cert_monitor.remember_domain("127.0.0.1")    # IP、无点的主机名不记
    cert_monitor.remember_domain("testserver")
    assert cert_monitor.current_domain() is None
    cert_monitor.remember_domain("Mail.Example.test")
    assert cert_monitor.current_domain() == "mail.example.test"
    alerts.clear()

    _fake_cert(monkeypatch, 60)
    status = cert_monitor.check_now()
    assert status["level"] == "ok" and status["days_left"] == 60 and not alerts

    _fake_cert(monkeypatch, 5)
    assert cert_monitor.check_now()["level"] == "warning"
    assert len(alerts) == 1 and "还有 5 天到期" in alerts[0]
    cert_monitor.check_now()                     # 同一天不重复提醒
    assert len(alerts) == 1

    _fake_cert(monkeypatch, -2)
    assert cert_monitor.check_now(alert=False)["level"] == "expired"
    _fake_cert(monkeypatch, 60, verify_error="Hostname mismatch")
    assert cert_monitor.check_now(alert=False)["level"] == "invalid"


def test_api_status_and_check(client, monkeypatch):
    login(client)
    token = login(client)
    _fake_cert(monkeypatch, 50)
    monkeypatch.setattr(cert_monitor, "CERT_DOMAIN", HOST)
    data = client.get("/api/cert").json()
    assert data["domain"] == HOST and data["status"] is None and data["renewal"]["agent"]["ok"] is False
    data = api_post(client, "/api/cert/check", token).json()
    assert data["status"]["level"] == "ok" and data["status"]["days_left"] == 50


def test_renew_request_and_result(client, monkeypatch, clean_state):
    alerts = clean_state
    token = login(client)
    cert_dir = cert_monitor.CERT_DIR

    # 续订脚本没装：拒绝
    resp = api_post(client, "/api/cert/renew", token, {"force": True})
    assert resp.status_code == 400 and "cert_renew.sh install" in resp.json()["detail"]

    cert_dir.mkdir(parents=True)
    (cert_dir / cert_monitor.HEARTBEAT_NAME).touch()
    resp = api_post(client, "/api/cert/renew", token, {"force": True})
    assert resp.status_code == 200 and resp.json()["renewal"]["pending"]["forced"] is True
    assert "force=1" in (cert_dir / cert_monitor.REQUEST_NAME).read_text().splitlines()
    assert api_post(client, "/api/cert/renew", token, {}).status_code == 400   # 已有请求在等待

    # 宿主机脚本处理完：删除请求、写回结果
    (cert_dir / cert_monitor.REQUEST_NAME).unlink()
    (cert_dir / cert_monitor.RESULT_NAME).write_text(
        "status=renewed\nfinished_at=2026-10-30T10:00:00+08:00\ntrigger=manual\nforced=1\n---\n"
        "Congratulations, all renewals succeeded:\n  /etc/letsencrypt/live/x/fullchain.pem (success)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(cert_monitor, "CERT_DOMAIN", HOST)
    _fake_cert(monkeypatch, 89)
    data = client.get("/api/cert").json()
    result = data["renewal"]["last_result"]
    assert result["status"] == "renewed" and result["label"] == "已续订" and result["forced"] is True
    assert "Congratulations" in result["output"] and data["renewal"]["pending"] is None
    assert data["status"]["days_left"] == 89                       # 有新结果后重新检测
    assert len(alerts) == 1 and "已续订" in alerts[0] and "剩 89 天" in alerts[0]
    with session_scope() as db:
        assert db.query(AuditLog).filter_by(event="cert_renew_requested").count() >= 1
        assert db.query(AuditLog).filter_by(event="cert_renewed").order_by(AuditLog.id.desc()).first().detail == "手动（强制）：已续订"
    client.get("/api/cert")                                        # 同一结果只通知一次
    assert len(alerts) == 1


def test_auto_result_only_notifies_on_change(monkeypatch, clean_state):
    alerts = clean_state
    cert_dir = cert_monitor.CERT_DIR
    cert_dir.mkdir(parents=True)
    result = cert_dir / cert_monitor.RESULT_NAME
    result.write_text("status=not_due\ntrigger=auto\nforced=0\n---\nNo renewals were attempted.\n", encoding="utf-8")
    assert cert_monitor.poll_result()["status"] == "not_due" and not alerts
    result.write_text("status=failed\ntrigger=auto\nforced=0\n---\nChallenge failed for domain\n", encoding="utf-8")
    os.utime(result, (result.stat().st_atime, result.stat().st_mtime + 5))
    assert cert_monitor.poll_result()["status"] == "failed"
    assert len(alerts) == 1 and "失败" in alerts[0] and "Challenge failed" in alerts[0]
