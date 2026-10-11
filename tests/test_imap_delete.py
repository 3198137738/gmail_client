"""删除邮件：移到 Gmail 的「已删除邮件」，而不是打 \\Deleted（在 Gmail 里只等于归档）。"""
from __future__ import annotations

from contextlib import contextmanager

import pytest

from app.services import imap_ops
from tests.helpers import create_account


class FakeClient:
    def __init__(self, folders, capabilities=(b"MOVE",)):
        self.folders = folders
        self.capabilities = capabilities
        self.calls = []

    def list_folders(self):
        return self.folders

    def select_folder(self, folder, readonly=True):
        self.calls.append(("select", folder))
        return {}

    def has_capability(self, cap):
        return cap.encode() in self.capabilities

    def move(self, uids, target):
        self.calls.append(("move", list(uids), target))

    def copy(self, uids, target):
        self.calls.append(("copy", list(uids), target))

    def delete_messages(self, uids):
        self.calls.append(("delete", list(uids)))

    def uid_expunge(self, uids):
        self.calls.append(("expunge", list(uids)))


GMAIL_FOLDERS = [
    ((b"\\HasNoChildren",), b"/", "INBOX"),
    ((b"\\HasNoChildren", b"\\Trash"), b"/", "[Gmail]/已删除邮件"),
    ((b"\\HasNoChildren", b"\\Junk"), b"/", "[Gmail]/垃圾邮件"),
]


@pytest.fixture()
def fake(monkeypatch):
    holder = {}

    @contextmanager
    def fake_imap_client(email, password, **kwargs):
        yield holder["client"]

    monkeypatch.setattr(imap_ops, "_imap_client", fake_imap_client)
    return holder


def test_delete_moves_to_trash(fake):
    fake["client"] = client = FakeClient(GMAIL_FOLDERS)
    acc = create_account()
    result = imap_ops.delete_imap_messages(acc, [("INBOX", [3, 4]), ("[Gmail]/垃圾邮件", [9])])
    assert result["ok"]
    assert ("move", [3, 4], "[Gmail]/已删除邮件") in client.calls
    assert ("move", [9], "[Gmail]/已删除邮件") in client.calls
    assert not any(call[0] == "delete" for call in client.calls)


def test_delete_without_move_capability_copies_then_expunges(fake):
    fake["client"] = client = FakeClient(GMAIL_FOLDERS, capabilities=())
    acc = create_account()
    assert imap_ops.delete_imap_messages(acc, [("INBOX", [5])])["ok"]
    assert client.calls[1:] == [
        ("copy", [5], "[Gmail]/已删除邮件"),
        ("delete", [5]),
        ("expunge", [5]),
    ]


def test_delete_without_trash_folder_falls_back_to_expunge(fake):
    fake["client"] = client = FakeClient([((b"\\HasNoChildren",), b"/", "INBOX")])
    acc = create_account()
    assert imap_ops.delete_imap_messages(acc, [("INBOX", [7])])["ok"]
    assert client.calls[1:] == [("delete", [7]), ("expunge", [7])]
