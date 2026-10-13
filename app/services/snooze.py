"""稍后提醒（延后）：延后的邮件到时间后回到收件箱最上面，并标为未读。

延后只在本客户端生效（Gmail 的 IMAP 不支持延后）：延后期间邮件仍在 Gmail 收件箱里，
本地列表中不显示。到时间时把邮件时间改为现在，使它排在最上面，并像新邮件一样推送通知。
"""
from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime

from sqlalchemy import select

from ..database import session_scope
from ..models import Message
from ..timeutil import utcnow
from .events import new_mail_preview, publish_new_mail
from .message_actions import toggle_messages_read_state

logger = logging.getLogger(__name__)


def wake_due() -> int:
    """把到时间的延后邮件放回收件箱，返回封数。"""
    with session_scope() as db:
        due = db.execute(
            select(Message).where(Message.snoozed_until.is_not(None), Message.snoozed_until <= utcnow())
        ).scalars().all()
        if not due:
            return 0
        now = datetime.now().replace(microsecond=0)   # 邮件时间是本地时间
        per_account: Counter[int] = Counter()
        woken: dict[int, list[int]] = {}
        for m in due:
            m.snoozed_until = None
            m.date = now
            per_account[int(m.account_id)] += 1
            woken.setdefault(int(m.account_id), []).append(int(m.id))
        toggle_messages_read_state(db, due, is_read=False)   # 会提交
        db.commit()
    for account_id, count in per_account.items():
        publish_new_mail(account_id, inbox=count, preview=new_mail_preview(account_id, count, woken[account_id]))
    logger.info("Snoozed messages returned to inbox: %s", sum(per_account.values()))
    return sum(per_account.values())


def safe_wake_due() -> None:
    try:
        wake_due()
    except Exception:
        logger.exception("Snooze wake-up failed")
