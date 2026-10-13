"""稍后提醒：延后期间不在收件箱，只在「已延后」中；到时间回到收件箱最上面并标为未读。"""
from __future__ import annotations

import time
from datetime import datetime, timedelta

from tests.helpers import api_post, create_account, create_message, login


def _ids(client, **query):
    return [m["id"] for m in client.get("/api/messages", params=query).json()["items"]]


def test_snoozed_message_moves_to_snoozed_folder(client):
    token = login(client)
    acc = create_account()
    mid = create_message(acc, is_read=True)
    until = int(time.time() * 1000) + 3600_000

    resp = api_post(client, "/api/messages/snooze", token, {"ids": [mid], "until_ms": until})
    assert resp.status_code == 200
    assert mid not in _ids(client, kind="inbox", account_id=acc)
    assert mid not in _ids(client, kind="all", account_id=acc)
    snoozed = client.get("/api/messages", params={"kind": "snoozed", "account_id": acc}).json()
    assert snoozed["kind_label"] == "已延后"
    assert [m["id"] for m in snoozed["items"]] == [mid]
    assert snoozed["items"][0]["snoozed_until"]

    # 取消延后
    api_post(client, "/api/messages/snooze", token, {"ids": [mid], "until_ms": None})
    assert mid in _ids(client, kind="inbox", account_id=acc)


def test_snooze_rejects_past_time(client):
    token = login(client)
    acc = create_account()
    mid = create_message(acc)
    resp = api_post(client, "/api/messages/snooze", token, {"ids": [mid], "until_ms": int(time.time() * 1000) - 1000})
    assert resp.status_code == 400


def test_due_snooze_returns_to_top_of_inbox_unread(client):
    from app.database import session_scope
    from app.models import Message
    from app.services.snooze import wake_due
    from app.timeutil import utcnow

    login(client)
    acc = create_account()
    old = create_message(acc, is_read=True, date=datetime(2020, 1, 1))
    newer = create_message(acc, date=datetime.now() - timedelta(minutes=5))
    with session_scope() as db:
        db.get(Message, old).snoozed_until = utcnow() - timedelta(seconds=1)

    assert wake_due() == 1
    with session_scope() as db:
        m = db.get(Message, old)
        assert m.snoozed_until is None and m.is_read is False
    assert _ids(client, kind="inbox", account_id=acc)[:2] == [old, newer]


def test_snoozed_unread_not_counted_in_sidebar(client):
    from app.database import session_scope
    from app.models import Message
    from app.timeutil import utcnow

    login(client)
    acc = create_account()
    mid = create_message(acc, is_read=False)
    with session_scope() as db:
        db.get(Message, mid).snoozed_until = utcnow() + timedelta(days=1)
    nav = client.get("/api/messages/nav").json()
    account = next(a for a in nav["accounts"] if a["id"] == acc)
    assert account["unread_inbox"] == 0
    assert nav["snoozed"] >= 1
