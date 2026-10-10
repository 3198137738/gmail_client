"""IMAP 同步分批：按大小切批，已入库的邮件不重复下载正文。"""
from __future__ import annotations

from email.message import EmailMessage

from app.services import imap_service
from app.services.imap_service import _plan_fetch_batches, _sync_one_folder
from tests.helpers import create_account, create_message

MB = 1024 * 1024


class FakeClient:
    def __init__(self, sizes: dict[int, int], raw: dict[int, bytes] | None = None):
        self.sizes = sizes
        self.raw = raw or {}
        self.fetch_calls: list[tuple[list[int], list[str]]] = []

    def fetch(self, uids, items):
        self.fetch_calls.append((list(uids), list(items)))
        if items == ["RFC822.SIZE"]:
            return {uid: {b"RFC822.SIZE": self.sizes[uid]} for uid in uids}
        return {
            uid: {b"RFC822": self.raw[uid], b"FLAGS": (), b"INTERNALDATE": None}
            for uid in uids
        }

    def select_folder(self, folder, readonly=True):
        return {b"UIDVALIDITY": 1}

    def search(self, criteria):
        return list(self.sizes)


def test_batches_respect_count_and_bytes():
    client = FakeClient({1: 1 * MB, 2: 1 * MB, 3: 5 * MB, 4: 30 * MB, 5: 1 * MB, 6: 1 * MB})
    batches = _plan_fetch_batches(client, [1, 2, 3, 4, 5, 6], max_count=3, max_bytes=8 * MB)
    # 1+2+3 = 7MB；4 单封超限独占一批；5、6 一批
    assert batches == [[1, 2, 3], [4], [5, 6]]


def test_batches_fall_back_to_count_when_size_unavailable():
    from imapclient.exceptions import IMAPClientError

    class Broken(FakeClient):
        def fetch(self, uids, items):
            raise IMAPClientError("nope")

    batches = _plan_fetch_batches(Broken({}), [1, 2, 3, 4, 5], max_count=2, max_bytes=MB)
    assert batches == [[1, 2], [3, 4], [5]]


def _raw_mail(subject: str) -> bytes:
    msg = EmailMessage()
    msg["From"] = "Alice <alice@example.com>"
    msg["To"] = "me@example.com"
    msg["Subject"] = subject
    msg.set_content("hello")
    return msg.as_bytes()


def test_sync_skips_downloading_existing_messages(monkeypatch):
    monkeypatch.setattr(imap_service, "submit_bg", lambda *a, **k: None)
    acc = create_account()
    create_message(acc, folder="INBOX", uid=101)  # 已入库
    client = FakeClient(
        {101: 100, 102: 100, 103: 100},
        raw={102: _raw_mail("new 102"), 103: _raw_mail("new 103")},
    )
    result = _sync_one_folder(client, acc, "INBOX", "inbox")

    assert result["saved"] == 2
    full_fetches = [uids for uids, items in client.fetch_calls if "RFC822" in items]
    assert full_fetches == [[102, 103]]
