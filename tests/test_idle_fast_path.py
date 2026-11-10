"""收到 IDLE 推送后的新邮件快速路径：一次 UID FETCH 直接取回新邮件，不 SELECT、不 SEARCH、不核对
已读 / 删除；没有新邮件时不入库；需要完整同步的情况返回 None；推送内容的分类。"""
from __future__ import annotations

from app.services import imap_service
from app.services.idle_service import _classify_push, _has_new_mail
from app.services.imap_service import _sync_one_folder, fast_sync_folder, ingest_new_messages
from tests.helpers import create_account
from tests.test_backfill import FakeMailbox, _count, _state, no_notify  # noqa: F401


class RecordingBox(FakeMailbox):
    """记录每条命令；``n:*`` 在没有更新的邮件时像真实服务器一样返回最后一封。"""

    def __init__(self, n: int):
        super().__init__(n)
        self.calls: list[tuple] = []

    def select_folder(self, folder, readonly=True):
        self.calls.append(("select", folder))
        return super().select_folder(folder, readonly)

    def search(self, criteria):
        self.calls.append(("search", list(criteria)))
        return super().search(criteria)

    def _expand(self, uids):
        if isinstance(uids, str) and uids.endswith(":*"):
            lo = int(uids.split(":")[0])
            top = max(self.uids, default=0)
            if lo > top:
                return [top] if top else []
        return super()._expand(uids)

    def fetch(self, uids, items, modifiers=None):
        self.calls.append(("fetch", uids if isinstance(uids, str) else list(uids), list(items)))
        return super().fetch(uids, items, modifiers)

    def arrive(self, count: int) -> list[int]:
        start = max(self.uids, default=0) + 1
        new = list(range(start, start + count))
        self.uids.extend(new)
        return new


def _prepared(n: int = 10):
    acc = create_account()
    box = RecordingBox(n)
    _sync_one_folder(box, acc, "INBOX", "inbox")
    box.calls.clear()
    return acc, box


def test_push_uses_single_fetch(no_notify):
    acc, box = _prepared()
    new = box.arrive(2)
    result = fast_sync_folder(box, acc, "INBOX", "inbox", uidvalidity=box.uidvalidity, trigger="push")
    assert result == {"saved": 2, "scanned": 2, "folder": "INBOX", "folder_kind": "inbox"}
    assert _count(acc) == 12 and _state(acc).last_uid == new[-1]
    # 只有一条命令：UID FETCH last+1:*，不 SELECT、不 SEARCH、不询问大小
    assert [c[0] for c in box.calls] == ["fetch"]
    assert box.calls[0][1] == "11:*" and "RFC822" in box.calls[0][2]


def test_push_without_new_mail_stores_nothing(no_notify):
    acc, box = _prepared()
    result = ingest_new_messages(box, acc, "INBOX", "inbox", uidvalidity=box.uidvalidity)
    assert result["saved"] == 0 and result["scanned"] == 0
    assert _count(acc) == 10 and _state(acc).last_uid == 10


def test_periodic_check_searches_first(no_notify):
    acc, box = _prepared()
    assert ingest_new_messages(box, acc, "INBOX", "inbox", uidvalidity=box.uidvalidity, trigger="periodic", bodies=False)["saved"] == 0
    assert [c[0] for c in box.calls] == ["search"]          # 没有新邮件：零下载
    box.calls.clear()
    box.arrive(3)
    assert ingest_new_messages(box, acc, "INBOX", "inbox", uidvalidity=box.uidvalidity, trigger="periodic", bodies=False)["saved"] == 3
    assert [c[0] for c in box.calls] == ["search", "fetch"] and box.calls[1][1] == [11, 12, 13]


def test_falls_back_to_full_sync_when_needed(no_notify, monkeypatch):
    acc = create_account()
    box = RecordingBox(5)
    # 还没首次同步
    assert ingest_new_messages(box, acc, "INBOX", "inbox", uidvalidity=box.uidvalidity) is None
    _sync_one_folder(box, acc, "INBOX", "inbox")
    # UIDVALIDITY 变了
    assert ingest_new_messages(box, acc, "INBOX", "inbox", uidvalidity=box.uidvalidity + 1) is None
    # 周期检查发现的新邮件太多
    monkeypatch.setattr(imap_service, "IDLE_FAST_PATH_MAX_MESSAGES", 2)
    box.arrive(3)
    assert ingest_new_messages(box, acc, "INBOX", "inbox", uidvalidity=box.uidvalidity, bodies=False) is None
    assert _count(acc) == 5
    # 完整同步照常把它们收进来
    _sync_one_folder(box, acc, "INBOX", "inbox")
    assert _count(acc) == 8


def test_fast_path_skips_already_stored(no_notify):
    acc, box = _prepared()
    new = box.arrive(2)
    _sync_one_folder(box, acc, "INBOX", "inbox")      # 定时同步先收到了
    assert _count(acc) == 12
    # 本地 last_uid 已是最新：推送再来时什么都不下载
    box.calls.clear()
    result = ingest_new_messages(box, acc, "INBOX", "inbox", uidvalidity=box.uidvalidity)
    assert result["saved"] == 0 and _count(acc) == 12 and new[-1] == _state(acc).last_uid


def test_classify_push():
    assert _classify_push([(12, b"EXISTS"), (1, b"RECENT")]) == (12, True, False)
    assert _classify_push([(3, b"EXPUNGE")]) == (None, False, True)
    assert _classify_push([(2, b"FETCH", (b"FLAGS", (b"\\Seen",)))]) == (None, False, True)
    assert _classify_push([(b"OK", b"Still here")]) == (None, False, False)
    assert _classify_push([(13, b"EXISTS"), (2, b"FETCH", ())]) == (13, True, True)
    assert _has_new_mail([(3, b"EXPUNGE")]) and not _has_new_mail([(b"OK", b"Still here")])
