"""桌面通知：new_mail 事件带上最新几封收件箱邮件的发件人、主题、验证码。"""
from __future__ import annotations

from datetime import timedelta

from app.database import session_scope
from app.models import Message
from app.services import events, snooze
from app.timeutil import utcnow
from tests.helpers import create_account, create_message


def test_preview_lists_newest_inbox_messages():
    acc = create_account()
    create_message(acc, subject="旧邮件")
    create_message(acc, subject="已发送", folder_kind="sent", folder="[Gmail]/Sent Mail")
    for i in range(4):
        create_message(acc, subject=f"新邮件 {i}", from_addr=f"Shop {i} <s{i}@x.example>")
    with session_scope() as db:
        newest = db.query(Message).filter_by(account_id=acc, subject="新邮件 3").one()
        newest.otp_code = "482913"

    preview = events.new_mail_preview(acc, 4)
    assert [p["subject"] for p in preview] == ["新邮件 3", "新邮件 2", "新邮件 1"]   # 最多 3 封
    assert preview[0]["from"] == "Shop 3" and preview[0]["otp_code"] == "482913"
    assert [p["subject"] for p in events.new_mail_preview(acc, 1)] == ["新邮件 3"]
    assert events.new_mail_preview(acc, 0) == []


def test_snooze_wake_previews_the_woken_messages(monkeypatch):
    acc = create_account()
    woken = create_message(acc, subject="到时间了")
    create_message(acc, subject="更新的普通邮件")
    with session_scope() as db:
        db.get(Message, woken).snoozed_until = utcnow() - timedelta(minutes=1)
    published = []
    monkeypatch.setattr(snooze, "publish_new_mail", lambda account_id, **kw: published.append((account_id, kw)))

    assert snooze.wake_due() >= 1
    mine = [kw for account_id, kw in published if account_id == acc]
    assert mine[0]["inbox"] == 1
    assert [p["subject"] for p in mine[0]["preview"]] == ["到时间了"]
