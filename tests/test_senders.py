"""发件人管理接口（routes/senders.py）：屏蔽、列表、解除屏蔽、信任。"""
from __future__ import annotations

from sqlalchemy import select

from app.database import session_scope
from app.models import BlockedSender, Message, TrustedSender
from tests.helpers import api_post, create_account, create_message, login


def test_block_list_and_unblock(client, _no_background_network):
    token = login(client)
    acc = create_account()
    mid = create_message(acc, from_addr="Spammer <spam@bad.example>")
    other = create_message(acc, from_addr="Spammer <spam@bad.example>")

    resp = api_post(client, f"/api/messages/{mid}/block-sender", token)
    assert resp.status_code == 200
    assert resp.json()["sender_email"] == "spam@bad.example"
    with session_scope() as db:
        assert db.execute(select(BlockedSender).where(
            BlockedSender.account_id == acc,
            BlockedSender.sender_email == "spam@bad.example",
        )).scalar_one_or_none() is not None
        # 同账号同发件人的已有邮件被隐藏（本地删除）
        assert db.get(Message, other) is None

    listing = client.get("/api/senders", params={"account_id": acc})
    assert listing.status_code == 200
    rows = listing.json()["blocked"]
    assert [r["sender_email"] for r in rows] == ["spam@bad.example"]
    assert client.get("/api/senders", params={"account_id": acc, "q": "nomatch"}).json()["blocked"] == []

    resp = api_post(client, f"/api/senders/blocked/{rows[0]['id']}/remove", token)
    assert resp.status_code == 200
    with session_scope() as db:
        assert db.execute(select(BlockedSender).where(
            BlockedSender.account_id == acc)).scalar_one_or_none() is None
    # 解除屏蔽后在后台补回屏蔽期间被忽略的邮件
    assert any(name == "restore_sender_messages_since" for name, _a, _k in _no_background_network)


def test_untrust_sender(client):
    token = login(client)
    acc = create_account()
    with session_scope() as db:
        row = TrustedSender(account_id=acc, sender_email="friend@ok.example")
        db.add(row)
        db.flush()
        tid = int(row.id)
    listing = client.get("/api/senders", params={"account_id": acc}).json()
    assert [r["sender_email"] for r in listing["trusted"]] == ["friend@ok.example"]
    assert api_post(client, f"/api/senders/trusted/{tid}/remove", token).status_code == 200
    with session_scope() as db:
        assert db.get(TrustedSender, tid) is None
    assert api_post(client, f"/api/senders/trusted/{tid}/remove", token).status_code == 404


def test_block_requires_csrf(client):
    login(client)
    acc = create_account()
    mid = create_message(acc, from_addr="x@bad.example")
    assert client.post(f"/api/messages/{mid}/block-sender", json={}).status_code == 403
