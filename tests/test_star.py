"""星标：切换后经 outbox 推送 \\Flagged，「已加星标」视图，服务器上的星标同步到本地。"""
from __future__ import annotations

from sqlalchemy import select

from app.database import session_scope
from app.models import Message, PendingOp
from app.services.imap_service import _sync_one_folder
from tests.helpers import create_account, create_message, login
from tests.test_backfill import FakeMailbox, no_notify  # noqa: F401


def _starred(mid: int) -> bool:
    with session_scope() as db:
        return bool(db.get(Message, mid).is_starred)


def test_toggle_star_via_fetch_returns_json_and_enqueues_flag(client):
    token = login(client)
    acc = create_account()
    mid = create_message(acc, uid=811)
    resp = client.post(
        f"/messages/{mid}/star",
        data={"starred": "1"},
        headers={"X-Requested-With": "fetch", "X-CSRF-Token": token},
    )
    assert resp.json() == {"id": mid, "starred": True}
    assert _starred(mid)
    with session_scope() as db:
        op = db.execute(select(PendingOp).where(PendingOp.account_id == acc)).scalar_one()
        assert (op.op, op.uids, op.arg) == ("add_flags", "811", "\\Flagged")

    resp = client.post(
        f"/messages/{mid}/star",
        headers={"X-Requested-With": "fetch", "X-CSRF-Token": token},
    )
    assert resp.json()["starred"] is False      # 不带参数时切换
    assert not _starred(mid)


def test_star_requires_csrf(client):
    login(client)
    mid = create_message(create_account())
    resp = client.post(f"/messages/{mid}/star", headers={"X-Requested-With": "fetch"})
    assert resp.status_code == 403
    assert not _starred(mid)


def test_starred_view_lists_only_starred_non_junk(client):
    login(client)
    acc = create_account()
    create_message(acc, subject="星标收件", is_starred=True)
    create_message(acc, subject="普通邮件")
    create_message(acc, subject="星标垃圾", folder="[Gmail]/Spam", folder_kind="junk", is_starred=True)
    html = client.get(f"/messages/?kind=starred&account_id={acc}").text
    assert "星标收件" in html
    assert "普通邮件" not in html and "星标垃圾" not in html
    assert "已加星标" in html


def test_row_and_detail_have_star_buttons(client):
    login(client)
    acc = create_account()
    mid = create_message(acc, is_starred=True)
    html = client.get(f"/messages/?account_id={acc}").text
    assert f'data-star-url="http://testserver/messages/{mid}/star"' in html
    assert "is-starred" in html
    detail = client.get(f"/messages/{mid}").text
    assert "gm-star-btn is-starred" in detail


def test_server_star_is_synced(no_notify):
    acc = create_account()
    box = FakeMailbox(3)
    box.modseq = 50
    box.flagged = {2}
    _orig_flags = box._flags
    box._flags = lambda uid: _orig_flags(uid) + ((b"\\Flagged",) if uid in box.flagged else ())
    _sync_one_folder(box, acc, "INBOX", "inbox")

    def starred_uids():
        with session_scope() as db:
            return set(db.execute(
                select(Message.uid).where(Message.account_id == acc, Message.is_starred.is_(True))
            ).scalars())

    assert starred_uids() == {2}         # 首次同步带上星标

    box.flagged = {3}                    # 在手机上：取消 2 的星标，给 3 加星标
    box.modseq += 1
    box.changed_since[2] = box.changed_since[3] = box.modseq
    _sync_one_folder(box, acc, "INBOX", "inbox")
    assert starred_uids() == {3}


def test_shortcut_targets_present(client):
    """键盘快捷键依赖的标记：脚本已加载，详情页按钮带 data-shortcut。"""
    login(client)
    acc = create_account()
    mid = create_message(acc)
    listing = client.get(f"/messages/?account_id={acc}").text
    assert "js/shortcuts.js" in listing
    assert 'data-shortcut="compose"' in listing
    detail = client.get(f"/messages/{mid}").text
    for name in ("back", "delete", "unread", "reply", "reply-all", "forward"):
        assert f'data-shortcut="{name}"' in detail, name
    assert "js/shortcuts.js" not in client.get("/login").text
