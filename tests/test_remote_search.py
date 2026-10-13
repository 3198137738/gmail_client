"""在 Gmail 服务器上搜索：X-GM-RAW 查询、只取邮件头、本地已有的邮件给出本地 id。"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime

from tests.helpers import create_account, create_message, login


class FakeIMAP:
    def __init__(self):
        self.calls = []

    def list_folders(self):
        return [((b"\HasNoChildren", b"\All"), b"/", "[Gmail]/All Mail")]

    def select_folder(self, folder, readonly=False):
        self.calls.append(("select", folder, readonly))

    def gmail_search(self, query):
        self.calls.append(("search", query))
        return [11, 12]

    def fetch(self, uids, items):
        self.calls.append(("fetch", list(uids), items))
        header = {
            11: b"From: Alice <a@example.com>\r\nSubject: =?utf-8?b?5a2j5bqm5oql5ZGK?=\r\nMessage-ID: <local@x>\r\n\r\n",
            12: b"From: Bob <b@example.com>\r\nSubject: archived\r\nMessage-ID: <remote@x>\r\n\r\n",
        }
        return {
            uid: {
                b"BODY[HEADER.FIELDS (FROM TO SUBJECT MESSAGE-ID)]": header[uid],
                b"INTERNALDATE": datetime(2020, 1, uid),
                b"X-GM-MSGID": 1000 + uid,
                b"FLAGS": (b"\Seen",) if uid == 11 else (),
            }
            for uid in uids
        }


def test_remote_search(client, monkeypatch):
    import app.services.remote_search as rs

    fake = FakeIMAP()

    @contextmanager
    def fake_client(email, password, **kw):
        yield fake

    monkeypatch.setattr(rs, "_imap_client", fake_client)
    login(client)
    acc = create_account("remote@example.com")
    local = create_message(acc, message_id="<local@x>")

    resp = client.get("/api/messages/remote-search", params={"q": "季度 after:2020-01-01", "account_id": acc})
    assert resp.status_code == 200
    data = resp.json()
    assert data["query"] == "季度 after:2020/01/01"
    assert ("select", "[Gmail]/All Mail", True) in fake.calls
    assert ("search", "季度 after:2020/01/01") in fake.calls
    items = data["accounts"][0]["items"]
    # 最新的在前
    assert [i["subject"] for i in items] == ["archived", "季度报告"]
    assert items[1]["local_id"] == local and items[0]["local_id"] is None
    assert items[0]["unread"] is True and items[1]["unread"] is False
    assert items[0]["gmail_url"].endswith(f"#all/{1012:x}")
