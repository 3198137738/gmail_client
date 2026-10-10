"""邮件列表的游标（keyset）分页。

列表固定按 ``date DESC NULLS LAST, id DESC`` 排序。翻页时把当前页第一封 /
最后一封邮件的 ``(date, id)`` 作为游标带到下一次请求，用 WHERE 条件定位，
不再使用 OFFSET——OFFSET 需要先扫描并丢弃前面所有行，翻得越深越慢。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from sqlalchemy import and_, or_

from ..models import Message

_SEP = "~"


@dataclass(frozen=True)
class Cursor:
    date: Optional[datetime]
    id: int

    def encode(self) -> str:
        date_part = self.date.isoformat() if self.date else ""
        return f"{date_part}{_SEP}{self.id}"

    @classmethod
    def decode(cls, raw: Optional[str]) -> Optional["Cursor"]:
        if not raw or _SEP not in raw:
            return None
        date_part, _, id_part = raw.rpartition(_SEP)
        try:
            msg_id = int(id_part)
            date = datetime.fromisoformat(date_part) if date_part else None
        except ValueError:
            return None
        return cls(date=date, id=msg_id)

    @classmethod
    def of(cls, message: Message) -> "Cursor":
        return cls(date=message.date, id=int(message.id))


def forward_order():
    """列表显示顺序：最新的在前，没有日期的排最后。"""
    return (Message.date.desc().nulls_last(), Message.id.desc())


def backward_order():
    """forward_order 的逆序，用于「上一页」「末页」查询，结果需再反转。"""
    return (Message.date.asc().nulls_first(), Message.id.asc())


def after(cursor: Cursor):
    """按显示顺序排在游标之后的邮件（下一页）。"""
    if cursor.date is None:
        return and_(Message.date.is_(None), Message.id < cursor.id)
    return or_(
        Message.date < cursor.date,
        and_(Message.date == cursor.date, Message.id < cursor.id),
        Message.date.is_(None),
    )


def before(cursor: Cursor):
    """按显示顺序排在游标之前的邮件（上一页）。"""
    if cursor.date is None:
        return or_(
            Message.date.is_not(None),
            and_(Message.date.is_(None), Message.id > cursor.id),
        )
    return or_(
        Message.date > cursor.date,
        and_(Message.date == cursor.date, Message.id > cursor.id),
    )
