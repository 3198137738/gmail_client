"""「仅看未读」筛选。"""
from __future__ import annotations

import re

from tests.helpers import create_account, create_message, login


def _subjects(html: str) -> set[str]:
    return set(re.findall(r"uf-(?:read|unread)-\d", html))


def test_unread_only_lists_unread(client):
    login(client)
    acc = create_account()
    create_message(acc, subject="uf-unread-1", is_read=False)
    create_message(acc, subject="uf-unread-2", is_read=False)
    create_message(acc, subject="uf-read-1", is_read=True)

    all_html = client.get("/messages/", params={"account_id": acc}).text
    assert _subjects(all_html) == {"uf-unread-1", "uf-unread-2", "uf-read-1"}
    assert 'aria-pressed="false"' in all_html

    html = client.get("/messages/", params={"account_id": acc, "unread": 1}).text
    assert _subjects(html) == {"uf-unread-1", "uf-unread-2"}
    assert 'aria-pressed="true"' in html
    assert "共 2 封" in html
    # 翻页、切换每页数量等链接都保留筛选
    assert "&amp;unread=1" in html


def test_unread_only_empty_state(client):
    login(client)
    acc = create_account()
    create_message(acc, subject="uf-read-2", is_read=True)
    html = client.get("/messages/", params={"account_id": acc, "unread": 1}).text
    assert "没有未读邮件" in html


def test_bulk_action_keeps_unread_filter(client):
    token = login(client)
    acc = create_account()
    mid = create_message(acc, is_read=False)
    resp = client.post(
        "/messages/batch-mark-read",
        data={"csrf_token": token, "message_ids": [mid], "kind": "inbox",
              "account_id": acc, "unread": "1"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "unread=1" in resp.headers["location"]
