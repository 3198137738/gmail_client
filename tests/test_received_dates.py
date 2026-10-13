"""邮件时间以 Gmail 收到时间（INTERNALDATE）为准，不信任发件方写的 Date 头。"""
from __future__ import annotations

from datetime import datetime

from tests.helpers import create_account, create_message

RAW = (
    b"From: Google <no-reply@accounts.google.com>\r\n"
    b"To: me@example.com\r\n"
    b"Subject: access expiring\r\n"
    # 发件方写错的日期：按 Date 排序会永远排在最上面
    b"Date: Wed, 17 Sep 2611 02:03:30 +0800\r\n"
    b"Message-ID: <x1@example.com>\r\n"
    b"\r\n"
    b"hello\r\n"
)


def test_parsed_message_uses_internaldate_not_date_header():
    from app.services.imap_service import _parse_fetched_messages

    received = datetime(2026, 10, 13, 1, 32, 41)
    acc = create_account()
    records = _parse_fetched_messages(
        account_id=acc, folder="INBOX", folder_kind="inbox",
        fetched_items=[(501, {b"RFC822": RAW, b"FLAGS": (), b"INTERNALDATE": received})],
        blocked_senders=set(),
    )
    assert records[0]["date"] == received


def test_date_header_used_when_internaldate_missing():
    from app.services.imap_service import _parse_fetched_messages

    acc = create_account()
    records = _parse_fetched_messages(
        account_id=acc, folder="INBOX", folder_kind="inbox",
        fetched_items=[(502, {b"RFC822": RAW, b"FLAGS": ()})],
        blocked_senders=set(),
    )
    assert records[0]["date"].year == 2611


class FakeClient:
    def __init__(self, dates: dict[int, datetime]):
        self.dates = dates
        self.fetches = []

    def fetch(self, criteria, items):
        self.fetches.append((criteria, items))
        return {uid: {b"INTERNALDATE": when} for uid, when in self.dates.items()}


def test_existing_messages_get_received_dates_once():
    from app.database import session_scope
    from app.models import Message
    from app.services.imap_service import _backfill_received_dates, _get_or_create_sync_state

    acc = create_account()
    bad = create_message(acc, uid=7, date=datetime(2611, 9, 17, 2, 3, 30))
    with session_scope() as db:
        _get_or_create_sync_state(db, acc, "INBOX").last_uid = 7

    received = datetime(2025, 3, 20, 9, 0, 0)
    client = FakeClient({7: received})
    assert _backfill_received_dates(client, acc, "INBOX") == 1
    assert client.fetches == [("1:7", ["INTERNALDATE"])]
    with session_scope() as db:
        assert db.get(Message, bad).date == received

    # 每个文件夹只补一次
    client.fetches.clear()
    assert _backfill_received_dates(client, acc, "INBOX") == 0
    assert client.fetches == []
