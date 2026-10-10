"""邮件解析：头部解码、内嵌图片 cid 替换、附件文件名、正文清洗。"""
from __future__ import annotations

from email.message import EmailMessage
from email.utils import format_datetime
from datetime import datetime, timezone
from pathlib import Path

from app.config import ATTACHMENTS_DIR
from app.services.email_parser import _safe_filename, parse_email

PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082"
)


def _rich_message() -> bytes:
    msg = EmailMessage()
    msg["Subject"] = "=?UTF-8?B?5Y+R56Wo6YCa55+l?="  # 发票通知
    msg["From"] = "=?UTF-8?B?5byg5LiJ?= <zhangsan@example.com>"  # 张三
    msg["To"] = "me@example.com, Bob <bob@example.com>"
    msg["Date"] = format_datetime(datetime(2026, 5, 1, 2, 0, tzinfo=timezone.utc))
    msg["Message-ID"] = "<abc@example.com>"
    msg.set_content("纯文本正文")
    msg.add_alternative(
        '<p onclick="x()">HTML 正文</p><img src="cid:logo123"><script>alert(1)</script>',
        subtype="html",
    )
    html_part = msg.get_payload()[1]
    html_part.add_related(PNG, maintype="image", subtype="png", cid="<logo123>",
                          filename="logo.png")
    msg.add_attachment(b"%PDF-1.4 fake", maintype="application", subtype="pdf",
                       filename="十月发票.pdf")
    return msg.as_bytes()


def test_headers_are_decoded():
    parsed = parse_email(_rich_message(), account_id=901, uid=1)
    assert parsed.subject == "发票通知"
    assert parsed.from_addr == "张三 <zhangsan@example.com>"
    assert parsed.to_addr == "me@example.com, Bob <bob@example.com>"
    assert parsed.message_id == "<abc@example.com>"
    # 日期转换为服务器本地时间（不带时区）
    expected = datetime(2026, 5, 1, 2, 0, tzinfo=timezone.utc).astimezone().replace(tzinfo=None)
    assert parsed.date == expected


def test_html_is_sanitized_and_cid_rewritten():
    parsed = parse_email(_rich_message(), account_id=902, uid=2)
    html = parsed.body_html
    assert "HTML 正文" in html
    assert "onclick" not in html and "<script" not in html and "alert" not in html
    assert "cid:" not in html
    assert "/attachments/902/inbox/2/inline/" in html
    assert parsed.body_text == "纯文本正文"


def test_attachments_saved_with_safe_names():
    parsed = parse_email(_rich_message(), account_id=903, uid=3)
    files = [a for a in parsed.attachments if not a.is_inline]
    inline = [a for a in parsed.attachments if a.is_inline]
    assert parsed.has_attachments
    assert len(files) == 1 and len(inline) == 1

    pdf = files[0]
    assert pdf.display_name == "十月发票.pdf"
    assert pdf.filename.endswith(".pdf") and pdf.filename.isascii()
    assert Path(pdf.file_path).read_bytes() == b"%PDF-1.4 fake"
    assert Path(pdf.file_path).resolve().is_relative_to(ATTACHMENTS_DIR.resolve())
    assert inline[0].content_id == "logo123"


def test_plain_text_only_message_is_escaped():
    msg = EmailMessage()
    msg["Subject"] = "plain"
    msg["From"] = "a@example.com"
    msg.set_content("<script>alert(1)</script> & more")
    parsed = parse_email(msg.as_bytes(), account_id=904, uid=4)
    assert "<script>" not in parsed.body_html
    assert "&lt;script&gt;" in parsed.body_html


def test_safe_filename_blocks_traversal():
    assert "/" not in _safe_filename("../../etc/passwd", "x")
    assert ".." not in _safe_filename("../../etc/passwd", "x")
    assert _safe_filename("", "fallback.bin") == "fallback.bin"
    assert _safe_filename("...", "fallback.bin") == "fallback.bin"
