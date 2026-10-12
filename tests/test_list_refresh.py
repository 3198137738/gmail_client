"""列表页实时刷新：轻量侧栏接口、「正在同步」只在整账号同步时显示。"""
from __future__ import annotations

from app.services import imap_client
from tests.helpers import create_account, create_message, login


def test_nav_endpoint_returns_unread_counts(client):
    login(client)
    acc = create_account()
    create_message(acc, is_read=False)
    create_message(acc, is_read=False)
    create_message(acc, folder="[Gmail]/Spam", folder_kind="junk", is_read=False)
    resp = client.get("/api/messages/nav")
    assert resp.status_code == 200
    nav = resp.json()
    (row,) = [a for a in nav["accounts"] if a["id"] == acc]
    assert (row["unread_inbox"], row["unread_junk"]) == (2, 1)
    assert nav["unread_inbox"] >= 2
    assert "items" not in nav    # 不查询邮件列表


def test_nav_endpoint_requires_login(client):
    resp = client.get("/api/messages/nav", follow_redirects=False)
    assert resp.status_code == 401


def test_busy_only_during_full_sync():
    acc = 424242
    lock = imap_client._account_lock(acc)
    # 标记已读、删除等短操作也持有账号锁，但不算「正在同步」
    assert lock.acquire(blocking=False)
    try:
        assert not imap_client.is_account_busy(acc)
    finally:
        imap_client._release_account_lock(acc, lock)

    assert imap_client._try_acquire_for_sync(acc, lock)
    try:
        assert imap_client.is_account_busy(acc)
    finally:
        imap_client._release_account_lock(acc, lock)
    assert not imap_client.is_account_busy(acc)


def test_list_does_not_show_syncing_for_short_ops(client):
    login(client)
    acc = create_account()
    create_message(acc)
    lock = imap_client._account_lock(acc)
    assert lock.acquire(blocking=False)   # 模拟后台正在推送已读标记
    try:
        data = client.get("/api/messages").json()
    finally:
        imap_client._release_account_lock(acc, lock)
    assert data["any_busy"] is False
