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


def test_block_selected_senders(client):
    token = login(client)
    acc = create_account(email="me@mine.example")
    other_acc = create_account()
    a = create_message(acc, from_addr="A <a@bad.example>")
    a2 = create_message(acc, from_addr="a@bad.example")
    b = create_message(other_acc, from_addr="B <b@bad.example>")
    sent = create_message(acc, from_addr="Me <me@mine.example>", folder_kind="sent")
    keep = create_message(acc, from_addr="b@bad.example")  # 同地址但在另一个账号屏蔽，不受影响

    resp = api_post(client, "/api/senders/block", token, json={"ids": [a, b, sent]})
    assert resp.status_code == 200
    data = resp.json()
    assert data["senders"] == ["a@bad.example", "b@bad.example"]
    assert data["added"] == 2 and data["hidden"] == 3 and data["skipped"] == 1
    with session_scope() as db:
        blocked = {(r.account_id, r.sender_email) for r in db.execute(
            select(BlockedSender).where(BlockedSender.account_id.in_([acc, other_acc]))).scalars()}
        assert blocked == {(acc, "a@bad.example"), (other_acc, "b@bad.example")}
        assert db.get(Message, a2) is None
        assert db.get(Message, sent) is not None
        assert db.get(Message, keep) is not None

    # 重复屏蔽不会报错，也不会重复添加
    c = create_message(acc, from_addr="a@bad.example")
    data = api_post(client, "/api/senders/block", token, json={"ids": [c]}).json()
    assert data["added"] == 0 and data["hidden"] == 1


def test_block_addresses_bulk(client):
    token = login(client)
    acc = create_account(email="me2@mine.example")
    existing = create_message(acc, from_addr="Ads <ads@spam.example>")
    text = "ads@spam.example, X <x@spam.example>;\ny@spam.example  me2@mine.example\nnot-an-address"
    resp = api_post(client, "/api/senders/blocked", token, json={"account_id": acc, "text": text})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "added": 3, "existing": 0, "hidden": 1}
    with session_scope() as db:
        emails = set(db.execute(select(BlockedSender.sender_email).where(BlockedSender.account_id == acc)).scalars())
        assert emails == {"ads@spam.example", "x@spam.example", "y@spam.example"}
        assert db.get(Message, existing) is None

    again = api_post(client, "/api/senders/blocked", token, json={"account_id": acc, "text": "ADS@spam.example"})
    assert again.json()["existing"] == 1
    assert api_post(client, "/api/senders/blocked", token, json={"account_id": acc, "text": "nothing"}).status_code == 400
    assert api_post(client, "/api/senders/blocked", token, json={"account_id": 9999, "text": "a@b.example"}).status_code == 404
