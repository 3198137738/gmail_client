"""CSP：禁止内联脚本，页面中不能出现内联 <script> 或 on* 事件属性。"""
from __future__ import annotations

import re

import pytest

from tests.helpers import create_account, create_message, login

# 允许外部脚本（src=）和 JSON 数据块（不会执行）
INLINE_SCRIPT_RE = re.compile(r"<script(?![^>]*(?:\ssrc=|type=\"application/json\"))[^>]*>", re.I)
INLINE_HANDLER_RE = re.compile(r"<[^>]+\son[a-z]+\s*=", re.I)


def test_csp_trusts_no_external_origins(client):
    csp = client.get("/login").headers["content-security-policy"]
    for directive in ("script-src", "style-src", "font-src"):
        part = next(p for p in csp.split(";") if p.strip().startswith(directive))
        assert "http" not in part, part


@pytest.mark.parametrize("path", ["/login", "/messages/"])
def test_pages_load_no_external_scripts_or_styles(client, path):
    if path != "/login":
        login(client)
    html = client.get(path).text
    # url_for 生成的是本站绝对地址（测试中为 testserver），其他主机一律不允许
    hosts = re.findall(r"<(?:script|link)[^>]+(?:src|href)=\"https?://([^/\"]+)", html)
    assert hosts and set(hosts) == {"testserver"}, hosts


def test_vendor_files_match_sri():
    """本地 Bootstrap 文件必须与模板里的 integrity 一致（被篡改时测试失败）。"""
    import base64
    import hashlib
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent / "app"
    base = (root / "templates" / "base.html").read_text(encoding="utf-8")
    pairs = re.findall(r"path='(vendor/[^']+)'\) }}\"\s+integrity=\"sha384-([^\"]+)\"", base)
    assert len(pairs) == 2
    for rel, expected in pairs:
        digest = hashlib.sha384((root / "static" / rel).read_bytes()).digest()
        assert base64.b64encode(digest).decode() == expected, rel


def test_csp_forbids_inline_scripts(client):
    resp = client.get("/login")
    csp = resp.headers["content-security-policy"]
    script_src = next(part for part in csp.split(";") if "script-src" in part)
    assert "'unsafe-inline'" not in script_src


@pytest.mark.parametrize(
    "path",
    ["/login", "/messages/", "/messages/?kind=junk", "/accounts/", "/compose/",
     "/compose/?embed=1", "/compose/sent", "/messages/blocked-senders", "/messages/999999"],
)
def test_pages_have_no_inline_scripts(client, path):
    if path != "/login":
        login(client)
    create_message(create_account(), subject="csp")
    html = client.get(path).text
    assert not INLINE_SCRIPT_RE.search(html), INLINE_SCRIPT_RE.search(html)
    assert not INLINE_HANDLER_RE.search(html), INLINE_HANDLER_RE.search(html)


def test_message_detail_has_no_inline_scripts(client):
    login(client)
    mid = create_message(create_account(), from_addr="O'Brien <o'brien@example.com>")
    html = client.get(f"/messages/{mid}").text
    assert not INLINE_SCRIPT_RE.search(html)
    assert not INLINE_HANDLER_RE.search(html)
    assert "data-confirm=" in html
