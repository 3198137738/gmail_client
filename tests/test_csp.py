"""CSP：禁止内联脚本、不信任外部来源；接口和前端页面都带上安全响应头。"""
from __future__ import annotations

import pytest

from tests.helpers import create_account, create_message, login


@pytest.fixture()
def spa_index(tmp_path, monkeypatch):
    """测试环境可能没有构建前端：用一个假的 index.html 代替。"""
    import app.main as main

    index = tmp_path / "index.html"
    index.write_text('<!doctype html><div id="app"></div>', encoding="utf-8")
    monkeypatch.setattr(main, "SPA_INDEX", index)
    return index


def _directive(csp: str, name: str) -> str:
    return next(p for p in csp.split(";") if p.strip().startswith(name))


def test_csp_trusts_no_external_origins(client, spa_index):
    csp = client.get("/login").headers["content-security-policy"]
    for directive in ("script-src", "style-src", "font-src", "connect-src"):
        assert "http" not in _directive(csp, directive)


def test_csp_forbids_inline_scripts(client, spa_index):
    csp = client.get("/login").headers["content-security-policy"]
    assert "'unsafe-inline'" not in _directive(csp, "script-src")


@pytest.mark.parametrize("path", ["/login", "/messages/", "/accounts/", "/compose/sent", "/messages/999999"])
def test_spa_pages_have_security_headers(client, spa_index, path):
    if path != "/login":
        login(client)
    resp = client.get(path)
    assert resp.status_code == 200
    assert "script-src 'self'" in resp.headers["content-security-policy"]
    assert resp.headers["x-frame-options"] == "SAMEORIGIN"
    assert resp.headers["x-content-type-options"] == "nosniff"
    # index.html 不能缓存：更新后要拿到新的脚本文件名
    assert resp.headers["cache-control"] == "no-cache"


def test_api_responses_have_csp(client):
    login(client)
    resp = client.get("/api/messages")
    assert resp.status_code == 200
    assert "script-src 'self'" in resp.headers["content-security-policy"]


def test_message_body_has_own_strict_csp(client):
    login(client)
    mid = create_message(create_account(), body_html="<p>hi</p>")
    csp = client.get(f"/messages/{mid}/body").headers["content-security-policy"]
    # 正文文档自带更严格的 CSP（不允许任何脚本），不会被页面 CSP 覆盖
    assert "default-src 'none'" in csp
    assert "script-src 'self'" not in csp
