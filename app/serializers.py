"""接口返回给前端的数据格式：时间转 ISO 字符串、地址解析出显示名等。

日期的「今天下午8:31 / 10月3日」等显示格式由前端按 ISO 字符串计算。
邮件的 ``date`` 字段已是服务器本地时间；其余 ``created_at`` 等 UTC 字段先转成本地时间。
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Optional

from .addresses import format_address, parse_list, parse_one
from .models import Attachment
from .timeutil import utc_to_local


def iso(value: Optional[datetime]) -> Optional[str]:
    """本地时间（不带时区）→ ISO 字符串；浏览器按本地时间解析。"""
    return value.isoformat(timespec="seconds") if value else None


def iso_utc(value: Optional[datetime]) -> Optional[str]:
    """数据库中的 UTC 时间 → 本地时间的 ISO 字符串。"""
    return iso(utc_to_local(value))


def addr_name(value: Optional[str]) -> str:
    """地址头中的显示名；没有显示名时返回邮箱地址。"""
    name, addr = parse_one(value)
    return (name or addr or (value or "")).strip()


def addr_email(value: Optional[str]) -> str:
    return parse_one(value)[1].strip()


def addr_list(value: Optional[str]) -> str:
    """完整地址列表「名字 <邮箱>」；名字为空或与邮箱相同时只显示邮箱。"""
    items = [format_address(name, addr) for name, addr in parse_list(value)]
    return ", ".join(i for i in items if i) or (value or "")


def recipients(value: Optional[str], me: Optional[str] = None) -> str:
    """「发送至 我、李娜」：收件人显示名，账号本人显示为「我」。"""
    names = []
    me_lower = (me or "").lower()
    for name, addr in parse_list(value):
        if me_lower and addr.lower() == me_lower:
            names.append("我")
        else:
            names.append(name or addr.split("@")[0] or addr)
    return "、".join(names)


_WS_RE = re.compile(r"\s+")


def snippet(value: Optional[str], length: int = 200) -> str:
    """正文摘要：合并空白，截断到 length 个字符。"""
    if not value:
        return ""
    return _WS_RE.sub(" ", str(value)).strip()[:length]


def attachment(att: Attachment) -> dict:
    name = att.display_name or att.filename
    return {
        "id": int(att.id),
        "name": name,
        "mime_type": (att.mime_type or "").lower(),
        "size": int(att.size or 0),
        "is_inline": bool(att.is_inline),
        "url": att.public_url,
        # 内嵌图片默认 inline 显示，下载时带上参数；普通附件默认就是下载
        "download_url": att.public_url + ("?download=true" if att.is_inline else ""),
    }
