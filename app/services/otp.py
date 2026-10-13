"""从邮件中识别验证码（一次性登录码）。

在「验证码 / code / OTP / コード」等关键词附近找 4～8 位的数字（允许中间有一个空格或横线，
如 123 456），或含数字的 6～8 位大写字母数字组合（如 A1B2C3）。先看主题，再看正文开头。
识别不到返回 None；宁可漏掉也不误报（例如订单号、年份、金额）。
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import select

from ..database import session_scope
from ..models import Message

logger = logging.getLogger(__name__)

_KEYWORDS = (
    r"验证码|校验码|动态码|确认码|认证码|安全码|登录码|激活码|驗證碼|認證碼|"
    r"verification\s*code|security\s*code|login\s*code|sign[\s-]*in\s*code|one[\s-]*time\s*(?:pass)?code|"
    r"passcode|\bOTP\b|\bPIN\b|\bcode\b|\bcodes\b|"
    r"コード|認証|確認番号|인증|코드|код|kode|código|codice|Bestätigungscode|kod"
)
_KEYWORD_RE = re.compile(_KEYWORDS, re.IGNORECASE)
# 关键词之后这么多个字符内找验证码（也会看关键词之前一小段，如「123456 是你的验证码」）
_AFTER = 120
_BEFORE = 40

# 前面不能紧挨数字、小数点、冒号等（日期、电话、金额的一部分）；「G-123456」这种字母前缀可以
_DIGITS_RE = re.compile(r"(?<![\d.,:/#])(?<!\d-)(\d{3,4}[ \-]\d{3,4}|\d{4,8})(?![\d.,:/%]|\s*(?:元|美元|USD|\$|%|px|年|月|日|号))")
_ALNUM_RE = re.compile(r"(?<![A-Za-z0-9])(?=[A-Z0-9]{6,8}(?![A-Za-z0-9]))(?=[A-Z]*\d)(?=\d*[A-Z])[A-Z0-9]{6,8}")
_YEAR_RE = re.compile(r"^(19|20)\d\d$")
# 正文只看开头一段：验证码通常在前面，后面的页脚里常有地址、电话
_BODY_CHARS = 3000


def _clean(code: str) -> str:
    return re.sub(r"[ \-]", "", code)


def _plausible(code: str) -> bool:
    digits = _clean(code)
    if not 4 <= len(digits) <= 8:
        return False
    if _YEAR_RE.match(digits):
        return False
    return len(set(digits)) > 1   # 0000、111111 之类多半不是


def _find_near_keywords(text: str) -> Optional[str]:
    for kw in _KEYWORD_RE.finditer(text):
        after = text[kw.end(): kw.end() + _AFTER]
        before = text[max(0, kw.start() - _BEFORE): kw.start()]
        for segment in (after, before):
            for m in _DIGITS_RE.finditer(segment):
                if _plausible(m.group(1)):
                    return _clean(m.group(1))
            m = _ALNUM_RE.search(segment)
            if m:
                return m.group(0)
    return None


def extract_code(subject: Optional[str], body_text: Optional[str]) -> Optional[str]:
    for text in (subject or "", (body_text or "")[:_BODY_CHARS]):
        if text:
            code = _find_near_keywords(" ".join(text.split()))
            if code:
                return code
    return None


# 升级后补算最近这些天的邮件（更早的验证码早已过期，没有意义）
BACKFILL_DAYS = 7


def backfill_recent() -> int:
    """为最近几天、还没识别过的邮件补上验证码，返回识别出的封数。"""
    since = datetime.now() - timedelta(days=BACKFILL_DAYS)
    found = 0
    with session_scope() as db:
        rows = db.execute(
            select(Message).where(Message.date >= since, Message.otp_code.is_(None), Message.folder_kind != "sent")
        ).scalars().all()
        for m in rows:
            code = extract_code(m.subject, m.body_text)
            if code:
                m.otp_code = code
                found += 1
    return found


def safe_backfill_recent() -> None:
    try:
        found = backfill_recent()
        if found:
            logger.info("Verification codes backfilled: %s", found)
    except Exception:
        logger.exception("Verification code backfill failed")
