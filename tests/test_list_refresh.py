"""列表页实时刷新：局部渲染、轻量侧栏接口、「正在同步」只在整账号同步时显示。"""
from __future__ import annotations

import re

from app.services import imap_client
from tests.helpers import create_account, create_message, login

FETCH = {"X-Requested-With": "fetch"}


def test_fetch_refresh_returns_only_nav_and_main(client):
    login(client)
    acc = create_account()
    create_message(acc, subject="局部刷新的邮件")
    full = client.get("/messages/").text
    part = client.get("/messages/", headers=FETCH).text

    assert "<header" in full and "<script" in full
    assert "<header" not in part
    assert "<script src=" not in part           # 不含页面脚本
    assert 'class="gm-nav' in part and 'class="gm-main"' in part
    assert "局部刷新的邮件" in part
    assert re.search(r"<title>.+ - Roundcube</title>", part)
    assert len(part) < len(full)


def test_nav_endpoint_renders_sidebar_with_unread_counts(client):
    login(client)
    acc = create_account()
    create_message(acc, is_read=False)
    create_message(acc, is_read=False)
    resp = client.get(f"/messages/nav?kind=inbox&account_id={acc}")
    assert resp.status_code == 200
    html = resp.text
    assert 'class="gm-nav' in html
    assert "gm-main" not in html and "rcm-row" not in html   # 不渲染邮件列表
    assert "<header" not in html


def test_nav_endpoint_requires_login(client):
    resp = client.get("/messages/nav", follow_redirects=False)
    assert resp.status_code == 303


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
        html = client.get("/messages/").text
    finally:
        imap_client._release_account_lock(acc, lock)
    assert "正在同步" not in html
