"""CSRF：写操作必须携带会话内的 token。"""
from __future__ import annotations

from tests.helpers import api_post, create_account, create_message, csrf_token, login


def test_login_without_token_rejected(client):
    csrf_token(client)   # 会话里已有 token，但请求没带
    resp = client.post("/api/login", json={"password": "test-password"})
    assert resp.status_code == 403


def test_post_without_token_rejected(client):
    login(client)
    mid = create_message(create_account(), is_read=True)
    resp = client.post(f"/api/messages/{mid}/mark-unread", json={})
    assert resp.status_code == 403
    assert resp.json()["detail"] == "页面已过期"


def test_post_with_wrong_token_rejected(client):
    login(client)
    mid = create_message(create_account(), is_read=True)
    resp = api_post(client, f"/api/messages/{mid}/mark-unread", "nope")
    assert resp.status_code == 403


def test_token_from_before_login_rejected(client):
    """登录会重建会话，登录前拿到的 token 随之失效。"""
    old = csrf_token(client)
    login(client)
    resp = api_post(client, "/api/sync", old)
    assert resp.status_code == 403


def test_post_with_token_accepted(client):
    token = login(client)
    mid = create_message(create_account(), is_read=True)
    resp = api_post(client, f"/api/messages/{mid}/mark-unread", token)
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_header_token_accepted(client):
    token = login(client)
    resp = api_post(client, "/api/sync", token)
    assert resp.status_code == 200


def test_multipart_form_token_accepted(client):
    """表单提交（如发信的 multipart）也可以用 csrf_token 字段代替请求头。"""
    token = login(client)
    resp = client.post(
        "/api/logout",
        data={"csrf_token": token},
        files={"dummy": ("a.txt", b"x", "text/plain")},
    )
    assert resp.status_code == 200


def test_multipart_without_token_rejected(client):
    login(client)
    resp = client.post(
        "/api/compose/send",
        data={"account_id": "1", "to": "a@example.com"},
        files={"attachments": ("a.txt", b"x", "text/plain")},
    )
    assert resp.status_code == 403
