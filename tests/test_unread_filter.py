"""「仅看未读」筛选。"""
from __future__ import annotations

from tests.helpers import api_post, create_account, create_message, login


def _list(client, **params):
    return client.get("/api/messages", params=params).json()


def _subjects(data) -> set[str]:
    return {m["subject"] for m in data["items"]}


def test_unread_only_lists_unread(client):
    login(client)
    acc = create_account()
    create_message(acc, subject="uf-unread-1", is_read=False)
    create_message(acc, subject="uf-unread-2", is_read=False)
    create_message(acc, subject="uf-read-1", is_read=True)

    data = _list(client, account_id=acc)
    assert _subjects(data) == {"uf-unread-1", "uf-unread-2", "uf-read-1"}
    assert data["unread_only"] is False

    data = _list(client, account_id=acc, unread=1)
    assert _subjects(data) == {"uf-unread-1", "uf-unread-2"}
    assert data["unread_only"] is True
    assert data["total"] == 2


def test_unread_only_empty(client):
    login(client)
    acc = create_account()
    create_message(acc, subject="uf-read-2", is_read=True)
    data = _list(client, account_id=acc, unread=1)
    assert data["items"] == [] and data["total"] == 0 and data["unread_only"] is True


def test_bulk_mark_read_removes_from_unread_view(client):
    token = login(client)
    acc = create_account()
    mid = create_message(acc, is_read=False)
    resp = api_post(client, "/api/messages/actions", token, {"action": "read", "ids": [mid]})
    assert resp.json() == {"ok": True, "affected": 1}
    assert _list(client, account_id=acc, unread=1)["total"] == 0


def test_mark_all_read_only_touches_current_view(client, monkeypatch):
    from app.services import outbox

    monkeypatch.setattr(outbox, "kick", lambda ids: None)
    token = login(client)
    acc = create_account()
    other = create_account()
    create_message(acc, is_read=False)
    create_message(acc, is_read=False)
    create_message(other, is_read=False)
    resp = api_post(client, "/api/messages/mark-all-read", token, {"kind": "inbox", "account_id": acc})
    assert resp.json()["affected"] == 2
    assert _list(client, account_id=acc, unread=1)["total"] == 0
    assert _list(client, account_id=other, unread=1)["total"] == 1
