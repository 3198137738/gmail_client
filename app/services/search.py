"""邮件搜索条件。

关键词 >= 3 个字符时走 FTS5 trigram 全文索引（迁移 0002 建立）；
更短的关键词 trigram 无法匹配，退回 LIKE 扫描。两种方式都是
「主题 / 发件人 / 收件人 / 正文」中包含该子串，大小写不敏感。
"""
from __future__ import annotations

from sqlalchemy import column, or_, select, table, text

from ..models import Message

FTS_MIN_CHARS = 3

_fts = table("messages_fts", column("rowid"))


def _escape_like(value: str) -> str:
    """转义 LIKE 通配符（以 ! 作为转义字符），让 % 和 _ 按字面匹配。"""
    return value.replace("!", "!!").replace("%", "!%").replace("_", "!_")


def message_search_filter(query: str | None):
    """返回可直接用于 ``.where()`` 的搜索条件；没有关键词时返回 None。"""
    q = (query or "").strip()
    if not q:
        return None
    if len(q) >= FTS_MIN_CHARS:
        # 整个关键词作为一个短语（双引号转义），即子串匹配
        phrase = '"' + q.replace('"', '""') + '"'
        matched = select(_fts.c.rowid).where(
            text("messages_fts MATCH :fts_query").bindparams(fts_query=phrase)
        )
        return Message.id.in_(matched)
    pattern = f"%{_escape_like(q)}%"
    return or_(
        Message.subject.ilike(pattern, escape="!"),
        Message.from_addr.ilike(pattern, escape="!"),
        Message.to_addr.ilike(pattern, escape="!"),
        Message.body_text.ilike(pattern, escape="!"),
    )
