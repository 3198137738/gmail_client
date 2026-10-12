"""时间工具。

数据库中的时间字段统一保存为「不带时区的 UTC 时间」。``datetime.utcnow()``
已在 Python 3.12 弃用，这里用带时区的 ``datetime.now(timezone.utc)`` 计算后
去掉时区信息，保持与已有数据一致，避免 aware/naive 混用导致比较报错。
"""
from __future__ import annotations

from datetime import datetime, timezone


def utcnow() -> datetime:
    """当前 UTC 时间（naive，与数据库中保存的格式一致）。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def utc_to_local(value: datetime | None) -> datetime | None:
    """数据库中 sent_at/created_at 等字段为 UTC（无时区），显示前转换为服务器本地时间。

    邮件的 ``date`` 字段已是本地时间，不需要转换。
    """
    if value is None:
        return None
    try:
        return value.replace(tzinfo=timezone.utc).astimezone().replace(tzinfo=None)
    except Exception:
        return value
