"""签名：账号可设置签名（经过清洗），写邮件时随账号列表返回。"""
from __future__ import annotations

from tests.helpers import api_post, create_account, login


def test_signature_saved_sanitized_and_returned_for_compose(client):
    token = login(client)
    acc = create_account()
    resp = api_post(client, f"/api/accounts/{acc}/update", token, {
        "signature_html": '<div>张三 · 技术部</div><script>alert(1)</script><a href="javascript:alert(1)">x</a>',
    })
    assert resp.status_code == 200
    account = next(a for a in client.get("/api/accounts").json()["accounts"] if a["id"] == acc)
    assert "张三 · 技术部" in account["signature_html"]
    assert "<script" not in account["signature_html"] and "javascript:" not in account["signature_html"]

    compose = client.get("/api/compose").json()
    assert next(a for a in compose["accounts"] if a["id"] == acc)["signature_html"] == account["signature_html"]

    # 清空签名
    api_post(client, f"/api/accounts/{acc}/update", token, {"signature_html": "  "})
    account = next(a for a in client.get("/api/accounts").json()["accounts"] if a["id"] == acc)
    assert account["signature_html"] == ""


def test_account_group_saved_and_in_nav(client):
    token = login(client)
    acc = create_account()
    api_post(client, f"/api/accounts/{acc}/update", token, {"group_name": "  注册用  "})
    nav = client.get("/api/messages/nav").json()
    assert next(a for a in nav["accounts"] if a["id"] == acc)["group"] == "注册用"
    api_post(client, f"/api/accounts/{acc}/update", token, {"group_name": ""})
    nav = client.get("/api/messages/nav").json()
    assert next(a for a in nav["accounts"] if a["id"] == acc)["group"] == ""
