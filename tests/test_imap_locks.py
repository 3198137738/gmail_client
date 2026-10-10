"""账号锁：被占用时登记待补同步，释放后自动补跑一次。"""
from __future__ import annotations

from app.services import imap_client
from app.services.imap_service import safe_sync_account


def test_deferred_resync_after_lock_release(monkeypatch):
    submitted = []
    monkeypatch.setattr(imap_client, "submit_bg", lambda fn, *a, **k: submitted.append((fn, a)))

    lock = imap_client._account_lock(4242)
    assert imap_client._try_acquire_for_sync(4242, lock)
    assert imap_client.is_account_busy(4242)
    # 第二个同步请求拿不到锁，登记待补同步
    assert not imap_client._try_acquire_for_sync(4242, lock)

    imap_client._release_account_lock(4242, lock)
    assert not imap_client.is_account_busy(4242)
    assert submitted == [(safe_sync_account, (4242,))]


def test_release_without_pending_does_not_resync(monkeypatch):
    submitted = []
    monkeypatch.setattr(imap_client, "submit_bg", lambda fn, *a, **k: submitted.append(fn))
    lock = imap_client._account_lock(4343)
    assert imap_client._try_acquire_for_sync(4343, lock)
    imap_client._release_account_lock(4343, lock)
    assert submitted == []
