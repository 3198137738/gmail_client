"""从邮件详情返回列表时回到原来的位置（账号、页码、筛选、搜索）。"""
from __future__ import annotations

from sqlalchemy import select

from app.database import session_scope
from app.models import BlockedSender
from tests.helpers import api_post, create_account, create_message, login


def test_detail_keeps_back_url_and_position(client):
    login(client)
    acc = create_account()
    for i in range(30):
        create_message(acc, subject=f"back-{i:02d}")

    back = f"/messages/?kind=inbox&account_id={acc}&q=&page=2&page_size=25"
    listing = client.get("/api/messages", params={"account_id": acc, "page": 2, "page_size": 25}).json()
    first = listing["items"][0]
    data = client.get(f"/api/messages/{first['id']}", params={"back": back}).json()
    assert data["back_url"] == back
    # 第 2 页第一封：列表中的第 26 封
    assert data["position"]["position"] == listing["first_index"] == 26
    assert data["position"]["total"] == 30
    assert data["position"]["newer_id"] and data["position"]["older_id"]


def test_back_param_must_be_message_list(client):
    login(client)
    mid = create_message(create_account())
    for bad in ("https://evil.example/", "//evil.example", "/accounts/", "/messages/blocked-senders"):
        back = client.get(f"/api/messages/{mid}", params={"back": bad}).json()["back_url"]
        assert back.startswith("/messages/?kind=")
        assert "evil" not in back and "accounts" not in back


def test_delete_one(client):
    token = login(client)
    acc = create_account()
    mid = create_message(acc)
    resp = api_post(client, f"/api/messages/{mid}/delete", token)
    assert resp.json() == {"ok": True}
    assert client.get(f"/api/messages/{mid}").status_code == 404


def test_block_sender_hides_existing_mail(client, _no_background_network):
    token = login(client)
    acc = create_account()
    mid = create_message(acc, from_addr="Spam <spam@bad.example>", folder_kind="junk")
    other = create_message(acc, from_addr="spam@bad.example", folder_kind="inbox")
    resp = api_post(client, f"/api/messages/{mid}/block-sender", token)
    data = resp.json()
    assert (data["sender_email"], data["hidden"]) == ("spam@bad.example", 2)
    assert data["job"]["items"][0]["sender"] == "spam@bad.example"
    assert client.get(f"/api/messages/{other}").status_code == 404
    with session_scope() as db:
        blocked = db.execute(select(BlockedSender.sender_email).where(BlockedSender.account_id == acc)).scalar_one()
    assert blocked == "spam@bad.example"
