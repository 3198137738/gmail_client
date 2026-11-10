"""特殊文件夹缓存：同一账号多次同步只发一次 LIST；失效或过期后重新发现；不传账号时不缓存。"""
from __future__ import annotations

from imapclient.exceptions import IMAPClientError

from app.services import imap_client
from app.services.imap_service import sync_targets
from tests.helpers import create_account
from tests.test_backfill import FakeMailbox


class ListingBox(FakeMailbox):
    def __init__(self):
        super().__init__(3)
        self.list_calls = 0
        self.fail_list = False
        self.folders = [
            ((b"\\HasNoChildren",), b"/", "INBOX"),
            ((b"\\HasNoChildren", b"\\Sent"), b"/", "[Gmail]/已发送邮件"),
            ((b"\\HasNoChildren", b"\\Trash"), b"/", "[Gmail]/已删除邮件"),
            ((b"\\HasNoChildren", b"\\Junk"), b"/", "[Gmail]/垃圾邮件"),
        ]

    def list_folders(self):
        self.list_calls += 1
        if self.fail_list:
            raise IMAPClientError("LIST failed")
        return list(self.folders)


def test_sync_targets_lists_once_per_account(monkeypatch):
    acc = create_account()
    box = ListingBox()
    targets = sync_targets(box, acc)
    assert ("[Gmail]/已发送邮件", "sent") in targets and ("[Gmail]/已删除邮件", "trash") in targets
    assert box.list_calls == 1                     # 已发送、已删除来自同一次 LIST
    assert sync_targets(box, acc) == targets
    assert box.list_calls == 1                     # 第二次同步不再 LIST
    # 其他标记也直接命中缓存
    assert imap_client._discover_junk_folder(box, acc) == "[Gmail]/垃圾邮件"
    assert imap_client._discover_special_folder(box, "\\All", acc) is None
    assert box.list_calls == 1

    imap_client.invalidate_folder_cache(acc)
    sync_targets(box, acc)
    assert box.list_calls == 2

    monkeypatch.setattr(imap_client, "FOLDER_CACHE_TTL_SEC", 0)
    sync_targets(box, acc)
    assert box.list_calls >= 3                     # 过期后重新发现


def test_failed_list_is_not_cached():
    acc = create_account()
    box = ListingBox()
    box.fail_list = True
    assert imap_client._discover_sent_folder(box, acc) is None
    box.fail_list = False
    assert imap_client._discover_sent_folder(box, acc) == "[Gmail]/已发送邮件"
    assert box.list_calls == 2


def test_without_account_id_no_cache():
    box = ListingBox()
    assert imap_client._discover_sent_folder(box) == "[Gmail]/已发送邮件"
    assert imap_client._discover_trash_folder(box) == "[Gmail]/已删除邮件"
    assert box.list_calls == 2
