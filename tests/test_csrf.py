"""CSRF：写操作必须携带会话内的 token。"""
from __future__ import annotations

from tests.helpers import create_account, create_message, login


def test_login_without_token_rejected(client):
    resp = client.post("/login", data={"password": "test-password"}, follow_redirects=False)
    assert resp.status_code == 403


def test_post_without_token_rejected(client):
    login(client)
    acc = create_account()
    mid = create_message(acc, is_read=True)
    resp = client.post(f"/messages/{mid}/mark-unread", follow_redirects=False)
    assert resp.status_code == 403


def test_post_with_wrong_token_rejected(client):
    login(client)
    acc = create_account()
    mid = create_message(acc, is_read=True)
    resp = client.post(f"/messages/{mid}/mark-unread", data={"csrf_token": "nope"},
                       follow_redirects=False)
    assert resp.status_code == 403


def test_post_with_token_accepted(client):
    token = login(client)
    acc = create_account()
    mid = create_message(acc, is_read=True)
    resp = client.post(f"/messages/{mid}/mark-unread", data={"csrf_token": token},
                       follow_redirects=False)
    assert resp.status_code == 303


def test_header_token_accepted(client):
    token = login(client)
    resp = client.post("/sync/", headers={"X-CSRF-Token": token}, follow_redirects=False)
    assert resp.status_code == 303


def test_multipart_form_token_accepted(client):
    token = login(client)
    resp = client.post(
        "/logout",
        data={"csrf_token": token},
        files={"dummy": ("a.txt", b"x", "text/plain")},
        follow_redirects=False,
    )
    assert resp.status_code == 303
