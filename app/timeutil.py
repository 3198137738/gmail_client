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
