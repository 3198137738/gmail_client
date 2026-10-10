"""发件人管理路由（routes/senders.py）：屏蔽、列表、解除屏蔽。"""
from __future__ import annotations

import re

from sqlalchemy import select

from app.database import session_scope
from app.models import BlockedSender, Message
from tests.helpers import create_account, create_message, login


def test_block_list_and_unblock(client):
    token = login(client)
    acc = create_account()
    mid = create_message(acc, from_addr="Spammer <spam@bad.example>")
    other = create_message(acc, from_addr="Spammer <spam@bad.example>")

    resp = client.post(f"/messages/{mid}/block-sender", data={"csrf_token": token},
                       follow_redirects=False)
    assert resp.status_code == 303
    with session_scope() as db:
        assert db.execute(select(BlockedSender).where(
            BlockedSender.account_id == acc,
            BlockedSender.sender_email == "spam@bad.example",
        )).scalar_one_or_none() is not None
        # 同账号同发件人的已有邮件被隐藏（本地删除）
        assert db.get(Message, other) is None

    page = client.get("/messages/blocked-senders", params={"account_id": acc})
    assert page.status_code == 200
    assert "spam@bad.example" in page.text
    action = re.search(r'action="([^"]*/blocked-senders/\d+/remove)"', page.text).group(1)

    resp = client.post(action, data={"csrf_token": token}, follow_redirects=False)
    assert resp.status_code == 303
    with session_scope() as db:
        assert db.execute(select(BlockedSender).where(
            BlockedSender.account_id == acc)).scalar_one_or_none() is None
