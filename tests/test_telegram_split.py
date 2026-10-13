"""Telegram 推送分段：先切纯文本再转义，不能切坏 HTML 实体或标签。"""
from __future__ import annotations

import re
from html import unescape

from app.services import telegram_service


def _capture(monkeypatch) -> list[str]:
    sent: list[str] = []
    monkeypatch.setattr(telegram_service, "send_message", lambda text, **kw: sent.append(text))
    return sent


def _valid_html(text: str) -> bool:
    # 每个 & 都是完整实体；标签成对出现
    if re.search(r"&(?!(amp|lt|gt|quot|#x27);)", text):
        return False
    return all(text.count(f"<{t}>") == text.count(f"</{t}>") for t in ("b", "i", "code"))


def test_long_body_full_of_entities_is_split_safely(monkeypatch):
    sent = _capture(monkeypatch)
    body = "a&b<c>" * 3000          # 没有换行，转义后长度约为 3 倍
    telegram_service._send_message_chunked("<b>新邮件</b>\n", body)
    assert len(sent) > 1
    assert all(len(piece) <= 4096 and _valid_html(piece) for piece in sent)
    joined = "".join(re.sub(r"\n\n<i>\(\d+/\d+\)</i>$", "", p) for p in sent)
    assert unescape(joined.replace("<b>新邮件</b>\n", "")) == body


def test_huge_recipient_list_keeps_header_within_limit(monkeypatch):
    sent = _capture(monkeypatch)

    class Msg:
        id = 1
        subject = "s"
        from_addr = "a@example.com"
        to_addr = ", ".join(f"user{i}@example.com" for i in range(500))
        cc_addr = None
        date = None
        body_text = "hello"
        body_html = None
        attachments = []
        otp_code = None

    telegram_service._notify_message_detail(Msg(), "me@example.com")
    assert len(sent) == 1 and len(sent[0]) <= 4096 and _valid_html(sent[0])
    assert "hello" in sent[0]
