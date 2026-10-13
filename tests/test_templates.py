"""邮件模板：增删改查，内容经过清洗。"""
from __future__ import annotations

from tests.helpers import api_post, login


def test_template_crud_and_sanitize(client):
    token = login(client)
    resp = api_post(client, "/api/templates", token, {
        "name": "  收到回复  ", "body_html": '<p>好的，收到。</p><script>alert(1)</script><img src=x onerror="alert(1)">',
    })
    assert resp.status_code == 200
    t = resp.json()
    assert t["name"] == "收到回复"
    assert "<script" not in t["body_html"] and "onerror" not in t["body_html"]
    assert "好的，收到。" in t["body_html"]

    assert any(i["id"] == t["id"] for i in client.get("/api/templates").json()["items"])

    resp = api_post(client, f"/api/templates/{t['id']}/update", token, {"name": "改名", "body_html": "<p>新内容</p>"})
    assert resp.json()["name"] == "改名"

    assert api_post(client, f"/api/templates/{t['id']}/delete", token).status_code == 200
    assert all(i["id"] != t["id"] for i in client.get("/api/templates").json()["items"])


def test_template_requires_name(client):
    token = login(client)
    assert api_post(client, "/api/templates", token, {"name": " ", "body_html": "x"}).status_code == 400
