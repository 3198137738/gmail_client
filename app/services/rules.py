"""自动规则（过滤器）：新邮件的发件人 / 收件人 / 主题包含指定文字时，自动标为已读、加星标、加标签或删除。

本地立即生效，对 Gmail 的修改经 outbox 推送（与手动操作相同）。只对增量同步收到的新邮件执行，
历史回填的旧邮件不执行（与 Gmail 过滤器一致）；设置页可以手动「对已有邮件执行」。
"""
from __future__ import annotations

import logging
from collections import defaultdict
from typing import Iterable, Optional

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from ..database import session_scope
from ..models import Label, MailRule, Message, MessageLabel
from . import audit, outbox
from .message_actions import delete_local_messages, set_starred, toggle_messages_read_state

logger = logging.getLogger(__name__)

FIELDS = {"from", "to", "subject", "any"}


def _escape_like(value: str) -> str:
    return value.replace("!", "!!").replace("%", "!%").replace("_", "!_")


def rule_condition(rule: MailRule):
    """规则的匹配条件（大小写不敏感的包含）。"""
    pattern = f"%{_escape_like(rule.contains.strip())}%"
    like = lambda col: col.ilike(pattern, escape="!")  # noqa: E731
    if rule.field == "to":
        cond = or_(like(Message.to_addr), like(Message.cc_addr))
    elif rule.field == "subject":
        cond = like(Message.subject)
    elif rule.field == "any":
        cond = or_(like(Message.from_addr), like(Message.to_addr), like(Message.cc_addr), like(Message.subject))
    else:
        cond = like(Message.from_addr)
    if rule.account_id:
        cond = cond & (Message.account_id == rule.account_id)
    return cond


def _add_label(db: Session, messages: list[Message], name: str) -> None:
    """加 Gmail 标签：本地记录标签关系，经 outbox 让 Gmail 也加上（标签不存在时 Gmail 会自动创建）。"""
    ops: dict[tuple[int, str], list[int]] = defaultdict(list)
    for m in messages:
        label = db.execute(
            select(Label).where(Label.account_id == m.account_id, Label.name == name)
        ).scalar_one_or_none()
        if label is None:
            label = Label(account_id=int(m.account_id), name=name)
            db.add(label)
            db.flush()
        exists = db.execute(
            select(MessageLabel).where(MessageLabel.message_id == m.id, MessageLabel.label_id == label.id)
        ).first()
        if exists is None:
            db.add(MessageLabel(message_id=int(m.id), label_id=int(label.id)))
            ops[(int(m.account_id), m.folder)].append(int(m.uid))
    for (account_id, folder), uids in ops.items():
        outbox.enqueue(db, account_id, folder, outbox.OP_ADD_LABEL, uids, name)
    db.commit()
    outbox.kick(list({a for a, _ in ops}))


def _delete(db: Session, messages: list[Message], rule: MailRule) -> None:
    grouped: dict[tuple[int, str], list[int]] = defaultdict(list)
    for m in messages:
        grouped[(int(m.account_id), m.folder)].append(int(m.uid))
    for (account_id, folder), uids in grouped.items():
        outbox.enqueue(db, account_id, folder, outbox.OP_DELETE, uids)
    audit.record(
        "mail_deleted",
        f"自动规则「{rule.name or rule.contains}」：" + audit.summarize_subjects([m.subject for m in messages]),
        source="system",
    )
    delete_local_messages(db, messages)
    outbox.kick(list({a for a, _ in grouped}))


def apply_rule(db: Session, rule: MailRule, messages: list[Message]) -> int:
    """对匹配的邮件执行规则的动作，返回邮件数。"""
    if not messages:
        return 0
    if rule.mark_read:
        toggle_messages_read_state(db, messages, is_read=True)
    if rule.star:
        set_starred(db, messages, True)
    if rule.label_name:
        _add_label(db, messages, rule.label_name)
    if rule.delete:
        _delete(db, messages, rule)
    db.get(MailRule, rule.id).hits += len(messages)
    db.commit()
    return len(messages)


def _matching(db: Session, rule: MailRule, extra: Iterable) -> list[Message]:
    return list(db.execute(
        select(Message)
        .options(selectinload(Message.attachments))
        .where(rule_condition(rule), Message.folder_kind.in_(("inbox", "junk")), *extra)
    ).scalars())


def apply_to_new(account_id: int, folder: str, uids: list[int]) -> int:
    """对刚同步到的新邮件执行启用中的规则，返回命中的邮件数（同一封命中多条规则时重复计数）。"""
    if not uids:
        return 0
    total = 0
    with session_scope() as db:
        rules = db.execute(
            select(MailRule).where(
                MailRule.enabled.is_(True),
                or_(MailRule.account_id.is_(None), MailRule.account_id == account_id),
            ).order_by(MailRule.id)
        ).scalars().all()
        for rule in rules:
            matched = _matching(db, rule, (
                Message.account_id == account_id, Message.folder == folder, Message.uid.in_(uids),
            ))
            total += apply_rule(db, rule, matched)
    if total:
        logger.info("Rules applied account=%s folder=%s matched=%s", account_id, folder, total)
    return total


def safe_apply_to_new(account_id: int, folder: str, uids: list[int]) -> None:
    try:
        apply_to_new(account_id, folder, uids)
    except Exception:
        logger.exception("Applying rules failed account=%s folder=%s", account_id, folder)


def apply_to_existing(rule_id: int) -> Optional[int]:
    """对已有邮件（收件箱、垃圾邮件）执行一条规则；规则不存在返回 None。"""
    with session_scope() as db:
        rule = db.get(MailRule, rule_id)
        if rule is None:
            return None
        return apply_rule(db, rule, _matching(db, rule, ()))
