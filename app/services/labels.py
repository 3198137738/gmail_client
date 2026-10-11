"""Gmail 标签（X-GM-LABELS）。

Gmail 把用户标签暴露为 IMAP 文件夹，邮件上的标签通过 X-GM-LABELS 读写。这里只处理用户标签：
收件箱、已发送、已加星标等系统标签（以反斜杠开头，或带特殊用途标志的文件夹）由其他功能负责。

本地只同步收件箱 / 已发送 / 垃圾邮件三个文件夹，所以标签视图中只有这些文件夹里带该标签的邮件；
在 Gmail 中已归档（不在收件箱）的邮件不会出现。
"""
from __future__ import annotations

import logging
from collections import defaultdict
from typing import Iterable, Optional

from imapclient import imap_utf7
from imapclient.exceptions import IMAPClientError
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..database import session_scope
from ..models import Label, Message, MessageLabel

logger = logging.getLogger(__name__)

# 带这些标志的文件夹是系统文件夹（所有邮件、已发送、垃圾邮件等），不是用户标签
_SYSTEM_FOLDER_FLAGS = {
    "\\noselect", "\\all", "\\archive", "\\sent", "\\drafts", "\\junk", "\\trash",
    "\\flagged", "\\important",
}


def _text(value) -> str:
    if isinstance(value, bytes):
        try:
            return imap_utf7.decode(value)
        except Exception:
            return value.decode("utf-8", "replace")
    return str(value)


def discover_label_names(client) -> list[str]:
    """从 IMAP 文件夹列表中找出用户标签。"""
    try:
        folders = client.list_folders()
    except IMAPClientError:
        logger.warning("IMAP LIST failed during label discovery", exc_info=True)
        return []
    names = []
    for flags, _delimiter, name in folders:
        name = _text(name)
        lowered = {_text(f).lower() for f in (flags or ())}
        if name.upper() == "INBOX" or lowered & _SYSTEM_FOLDER_FLAGS:
            continue
        names.append(name)
    return sorted(set(names))


def parse_labels(raw) -> set[str]:
    """X-GM-LABELS 的值 → 用户标签名集合（去掉 \\Inbox、\\Starred 等系统标签）。"""
    result = set()
    for item in raw or ():
        name = _text(item).strip()
        if name and not name.startswith("\\"):
            result.add(name)
    return result


def sync_label_list(account_id: int, names: Iterable[str]) -> None:
    """让本地标签列表与 Gmail 一致：新增缺少的，删除 Gmail 中已不存在的。"""
    wanted = set(names)
    with session_scope() as db:
        existing = {
            label.name: label
            for label in db.execute(select(Label).where(Label.account_id == account_id)).scalars()
        }
        for name in wanted - set(existing):
            db.add(Label(account_id=account_id, name=name))
        for name in set(existing) - wanted:
            db.delete(existing[name])


def label_ids(db: Session, account_id: int, names: Iterable[str]) -> dict[str, int]:
    """标签名 → id；本地还没有的标签（标签列表尚未同步）顺便创建。"""
    names = set(names)
    if not names:
        return {}
    found = {
        name: int(label_id)
        for label_id, name in db.execute(
            select(Label.id, Label.name).where(Label.account_id == account_id, Label.name.in_(names))
        ).all()
    }
    for name in names - set(found):
        label = Label(account_id=account_id, name=name)
        db.add(label)
        db.flush()
        found[name] = int(label.id)
    return found


def set_message_labels(db: Session, account_id: int, message_id: int, names: Iterable[str]) -> None:
    ids = set(label_ids(db, account_id, names).values())
    current = set(db.execute(select(MessageLabel.label_id).where(MessageLabel.message_id == message_id)).scalars())
    if current - ids:
        db.execute(delete(MessageLabel).where(
            MessageLabel.message_id == message_id, MessageLabel.label_id.in_(current - ids)
        ))
    for label_id in ids - current:
        db.add(MessageLabel(message_id=message_id, label_id=label_id))


def apply_remote_labels(
    account_id: int, folder: str, labels_by_uid: dict[int, set[str]], skip_uids: Optional[set[int]] = None
) -> int:
    """用服务器上的标签覆盖本地这些邮件的标签，返回改动的邮件数。"""
    skip_uids = skip_uids or set()
    changed = 0
    with session_scope() as db:
        rows = db.execute(
            select(Message.id, Message.uid).where(
                Message.account_id == account_id,
                Message.folder == folder,
                Message.uid.in_([u for u in labels_by_uid if u not in skip_uids]),
            )
        ).all() if labels_by_uid else []
        current = labels_by_message(db, [int(r.id) for r in rows])
        for message_id, uid in rows:
            wanted = labels_by_uid[int(uid)]
            if {label["name"] for label in current.get(int(message_id), [])} != wanted:
                set_message_labels(db, account_id, int(message_id), wanted)
                changed += 1
    return changed


def labels_by_message(db: Session, message_ids: list[int]) -> dict[int, list[dict]]:
    """列表 / 详情页显示用：邮件 id → [{id, name}]（按名称排序）。"""
    if not message_ids:
        return {}
    result: dict[int, list[dict]] = defaultdict(list)
    for message_id, label_id, name in db.execute(
        select(MessageLabel.message_id, Label.id, Label.name)
        .join(Label, Label.id == MessageLabel.label_id)
        .where(MessageLabel.message_id.in_(message_ids))
        .order_by(Label.name)
    ).all():
        result[int(message_id)].append({"id": int(label_id), "name": name})
    return dict(result)


def short_name(name: str) -> str:
    """嵌套标签只显示最后一级（Gmail 侧栏中的做法），完整名称放在提示里。"""
    return name.rsplit("/", 1)[-1]
