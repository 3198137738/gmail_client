"""定时发送：每分钟检查一次，把到时间的邮件发出。

附件在安排时已保存到 ATTACHMENTS_DIR/scheduled/<id>/；发送成功后删除记录和附件，
发送失败保留记录（status=failed）并显示原因，用户可以「取消并编辑」或「立即重试」。
"""
from __future__ import annotations

import logging
from datetime import timedelta
from pathlib import Path

from sqlalchemy import select

from ..config import ATTACHMENTS_DIR
from ..database import session_scope
from ..models import Account, ScheduledSend
from ..timeutil import utcnow
from .outgoing import deliver, remove_files

logger = logging.getLogger(__name__)

SCHEDULED_DIR = ATTACHMENTS_DIR / "scheduled"
# 「正在发送」超过这么久还没结束，说明发送过程中进程退出了
_STUCK_AFTER = timedelta(minutes=10)


def attachments_of(item: ScheduledSend) -> list[Path]:
    if not item.attachments_dir:
        return []
    directory = Path(item.attachments_dir)
    return sorted(p for p in directory.iterdir() if p.is_file()) if directory.is_dir() else []


def _send_one(item_id: int) -> None:
    with session_scope() as db:
        item = db.get(ScheduledSend, item_id)
        if item is None or item.status != "sending":
            return
        account = db.get(Account, item.account_id)
        if account is None or not account.enabled:
            item.status = "failed"
            item.error = "发件账号不可用（可能已停用或删除）"
            return
        record = deliver(
            db, account,
            to=item.to_addr, cc=item.cc_addr, bcc=item.bcc_addr, subject=item.subject or "",
            body_text=item.body_text, body_html=item.body_html, use_ssl=bool(item.use_ssl),
            in_reply_to=item.in_reply_to, references=item.references,
            attachments=attachments_of(item), audit_source="system",
        )
        if record.status == "failed":
            item.status = "failed"
            item.error = record.error
            logger.warning("Scheduled send failed id=%s: %s", item_id, record.error)
            return
        directory = Path(item.attachments_dir) if item.attachments_dir else None
        db.delete(item)
    if directory is not None:
        remove_files([], directory)
    logger.info("Scheduled send delivered id=%s", item_id)


def process_due() -> int:
    """发出所有到时间的定时邮件，返回尝试发送的封数。"""
    now = utcnow()
    with session_scope() as db:
        for stuck in db.execute(
            select(ScheduledSend).where(ScheduledSend.status == "sending", ScheduledSend.send_at <= now - _STUCK_AFTER)
        ).scalars():
            stuck.status = "failed"
            stuck.error = "发送过程中断，请到「已发送」确认是否已发出后再重试"
        due = list(db.execute(
            select(ScheduledSend.id)
            .where(ScheduledSend.status == "pending", ScheduledSend.send_at <= now)
            .order_by(ScheduledSend.send_at)
        ).scalars())
        # 先标记为正在发送，并发运行的另一个任务不会重复发送
        for item_id in due:
            db.get(ScheduledSend, item_id).status = "sending"
    for item_id in due:
        try:
            _send_one(item_id)
        except Exception:
            logger.exception("Scheduled send crashed id=%s", item_id)
            with session_scope() as db:
                item = db.get(ScheduledSend, item_id)
                if item is not None:
                    item.status = "failed"
                    item.error = "发送时出现内部错误"
    return len(due)


def safe_process_due() -> None:
    try:
        process_due()
    except Exception:
        logger.exception("Scheduled send job failed")
