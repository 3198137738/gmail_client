"""邮件搜索条件。

支持与 Gmail 相同写法的搜索条件，可与普通关键词组合，例如
``from:alice subject:"季度报告" has:attachment after:2026-01-01 发票``：

- ``from:`` 发件人、``to:`` 收件人（含抄送）、``subject:`` 主题：包含该文字；
- ``has:attachment`` 有附件；``is:unread`` / ``is:read`` / ``is:starred``；
- ``after:`` / ``before:`` 日期（``2026-01-01`` 或 ``2026/1/1``），after 含当天，before 不含当天；
- 值中有空格时用双引号括起来；不认识的条件按普通关键词处理。

其余的普通关键词作为一个整体（子串）匹配「主题 / 发件人 / 收件人 / 正文」，大小写不敏感：
>= 3 个字符时走 FTS5 trigram 全文索引（迁移 0002 建立）；更短的 trigram 无法匹配，退回 LIKE 扫描。
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import and_, column, or_, select, table, text

from ..models import Message

FTS_MIN_CHARS = 3

_fts = table("messages_fts", column("rowid"))

# key:"带空格的值" | key:值 | "带空格的关键词" | 关键词
_TOKEN_RE = re.compile(r'(\w+):"([^"]*)"|(\w+):(\S+)|"([^"]*)"|(\S+)')
_DATE_RE = re.compile(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})$")


def _escape_like(value: str) -> str:
    """转义 LIKE 通配符（以 ! 作为转义字符），让 % 和 _ 按字面匹配。"""
    return value.replace("!", "!!").replace("%", "!%").replace("_", "!_")


def _contains(col, value: str):
    return col.ilike(f"%{_escape_like(value)}%", escape="!")


def _parse_date(value: str) -> Optional[datetime]:
    m = _DATE_RE.match(value.strip())
    if not m:
        return None
    try:
        return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def _text_filter(q: str):
    if len(q) >= FTS_MIN_CHARS:
        # 整个关键词作为一个短语（双引号转义），即子串匹配
        phrase = '"' + q.replace('"', '""') + '"'
        matched = select(_fts.c.rowid).where(
            text("messages_fts MATCH :fts_query").bindparams(fts_query=phrase)
        )
        return Message.id.in_(matched)
    return or_(
        _contains(Message.subject, q),
        _contains(Message.from_addr, q),
        _contains(Message.to_addr, q),
        _contains(Message.body_text, q),
    )


def _operator(key: str, value: str):
    """搜索条件对应的 SQL 条件；不认识的条件返回 None（按普通关键词处理）。"""
    key = key.lower()
    if not value:
        return None
    if key == "from":
        return _contains(Message.from_addr, value)
    if key in ("to", "cc"):
        return or_(_contains(Message.to_addr, value), _contains(Message.cc_addr, value))
    if key == "subject":
        return _contains(Message.subject, value)
    if key == "has" and value.lower() in ("attachment", "attachments", "附件"):
        return Message.has_attachments.is_(True)
    if key == "is":
        v = value.lower()
        if v in ("unread", "未读"):
            return Message.is_read.is_(False)
        if v in ("read", "已读"):
            return Message.is_read.is_(True)
        if v in ("starred", "星标"):
            return Message.is_starred.is_(True)
        return None
    if key in ("after", "before"):
        day = _parse_date(value)
        if day is None:
            return None
        return Message.date >= day if key == "after" else Message.date < day
    if key in ("older_than", "newer_than"):
        m = re.fullmatch(r"(\d+)([dmy])", value.lower())
        if not m:
            return None
        days = int(m.group(1)) * {"d": 1, "m": 30, "y": 365}[m.group(2)]
        cutoff = datetime.now() - timedelta(days=days)
        return Message.date < cutoff if key == "older_than" else Message.date >= cutoff
    return None


def message_search_filter(query: str | None):
    """返回可直接用于 ``.where()`` 的搜索条件；没有关键词时返回 None。"""
    q = (query or "").strip()
    if not q:
        return None
    conditions = []
    words: list[str] = []
    for key_q, value_q, key, value, quoted, word in _TOKEN_RE.findall(q):
        k, v = (key_q, value_q) if key_q else (key, value)
        if k:
            cond = _operator(k, v)
            if cond is not None:
                conditions.append(cond)
            else:
                words.append(f"{k}:{v}")
        else:
            words.append(quoted or word)
    rest = " ".join(w for w in words if w).strip()
    if rest:
        conditions.append(_text_filter(rest))
    if not conditions:
        return None
    return conditions[0] if len(conditions) == 1 else and_(*conditions)
