"""CSP：禁止内联脚本，页面中不能出现内联 <script> 或 on* 事件属性。"""
from __future__ import annotations

import re

import pytest

from tests.helpers import create_account, create_message, login

# 允许外部脚本（src=）和 JSON 数据块（不会执行）
INLINE_SCRIPT_RE = re.compile(r"<script(?![^>]*(?:\ssrc=|type=\"application/json\"))[^>]*>", re.I)
INLINE_HANDLER_RE = re.compile(r"<[^>]+\son[a-z]+\s*=", re.I)


def test_csp_forbids_inline_scripts(client):
    resp = client.get("/login")
    csp = resp.headers["content-security-policy"]
    script_src = next(part for part in csp.split(";") if "script-src" in part)
    assert "'unsafe-inline'" not in script_src


@pytest.mark.parametrize(
    "path",
    ["/login", "/messages/", "/messages/?kind=junk", "/accounts/", "/compose/",
     "/compose/sent", "/messages/blocked-senders"],
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
