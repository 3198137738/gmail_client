"""Shared Jinja2 environment setup."""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone

from fastapi.templating import Jinja2Templates
from markupsafe import Markup, escape

from .addresses import format_address, parse_list, parse_one
from .csrf import csrf_input, csrf_token
from .navigation import sidebar_data
from .config import REALTIME_ENABLED, STATIC_DIR, SYNC_JUNK_ENABLED, TEMPLATES_DIR, WEB_AUTH_ENABLED

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _static_version() -> str:
    """根据静态文件内容计算版本号，附加到静态资源 URL 上（?v=...）。

    反向代理（nginx proxy_cache）和浏览器都会长期缓存 CSS/JS；文件内容一变，
    URL 随之改变，就能立即拿到新文件，而不必手动清缓存。
    """
    digest = hashlib.sha1()
    for path in sorted(STATIC_DIR.rglob("*")):
        if path.is_file():
            digest.update(path.relative_to(STATIC_DIR).as_posix().encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()[:10]


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


def _fmt_dt(value, fmt: str = "%Y-%m-%d %H:%M") -> str:
    if value is None:
        return ""
    try:
        return value.strftime(fmt)
    except Exception:
        return str(value)


def _fmt_rel_dt(value) -> str:
    """Render a datetime as a short, human-friendly relative string.

    Comparison uses :func:`datetime.now` (local time) because the IMAP
    parser stores mail dates as **local naive** datetimes (see
    ``email_parser._parse_date``); using ``utcnow`` here would offset every
    calculation by the server's timezone and misclassify today's mail as
    "in the future" on hosts east of UTC.

    Display rules (per UX preference):
        - Same calendar day  -> ``HH:MM``        (e.g. ``10:04``)
        - Yesterday          -> ``昨天``
        - 2-6 days ago       -> ``N 天前``
        - Same calendar year -> ``MM-DD``        (e.g. ``04-21``)
        - Older              -> ``YYYY-MM-DD``
    """
    if value is None:
        return ""
    try:
        now = datetime.now()
        value_date = value.date()
        today = now.date()
    except Exception:
        return str(value)

    days_diff = (today - value_date).days

    # Future-dated mail (clock skew or odd Date headers) - treat as today
    # so we still get a clean ``HH:MM`` instead of a confusing past format.
    if days_diff <= 0:
        try:
            return value.strftime("%H:%M")
        except Exception:
            return str(value)
    if days_diff == 1:
        return "昨天"
    if days_diff < 7:
        return f"{days_diff} 天前"
    try:
        if value.year == now.year:
            return value.strftime("%m-%d")
        return value.strftime("%Y-%m-%d")
    except Exception:
        return str(value)


def _truncate(value: str, length: int = 80) -> str:
    if not value:
        return ""
    text = str(value)
    return text if len(text) <= length else text[: length - 1] + "\u2026"


def _initials(value: str) -> str:
    """Return up to two leading letters for a stylised avatar bubble."""
    if not value:
        return "?"
    text = str(value).strip()
    if not text:
        return "?"
    if "@" in text:
        text = text.split("@", 1)[0]
    parts = [p for p in text.replace(".", " ").replace("_", " ").split() if p]
    if not parts:
        return text[0].upper()
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[1][0]).upper()


def _addr_name(value: str | None) -> str:
    """地址头中的显示名；没有显示名时返回邮箱地址。"""
    name, addr = parse_one(value)
    return (name or addr or (value or "")).strip()


def _addr_email(value: str | None) -> str:
    """地址头中的邮箱地址部分。"""
    return parse_one(value)[1].strip()


def _avatar_tone(value: str | None) -> int:
    """根据名称稳定地选一种头像配色（0-5），同一发件人颜色固定。"""
    return sum(ord(ch) for ch in (value or "")) % 6


def _icon(name: str, filled: bool = False, cls: str = "") -> Markup:
    """Material Symbols 图标（与 Gmail 同一套图标）。filled=True 为实心样式。"""
    classes = "ms" + (" ms-fill" if filled else "") + (f" {cls}" if cls else "")
    return Markup(f'<span class="{escape(classes)}" aria-hidden="true">{escape(name)}</span>')


_WEEKDAYS = "一二三四五六日"


def _cn_time(value: datetime) -> str:
    """Gmail 中文界面的时间：上午/下午 + 12 小时制，如「下午8:31」。"""
    hour = value.hour
    period = "上午" if hour < 12 else "下午"
    hour12 = hour % 12 or 12
    return f"{period}{hour12}:{value.minute:02d}"


def _gmail_list_date(value) -> str:
    """邮件列表日期（Gmail 中文）：今天「下午8:31」，今年「10月3日」，更早「2025/10/3」。"""
    if value is None:
        return ""
    now = datetime.now()
    if value.date() >= now.date():
        return _cn_time(value)
    if value.year == now.year:
        return f"{value.month}月{value.day}日"
    return f"{value.year}/{value.month}/{value.day}"


def _relative_ago(value: datetime) -> str:
    seconds = (datetime.now() - value).total_seconds()
    if seconds < 0:
        return ""
    if seconds < 60:
        return "刚刚"
    if seconds < 3600:
        return f"{int(seconds // 60)}分钟前"
    if seconds < 86400:
        return f"{int(seconds // 3600)}小时前"
    if seconds < 7 * 86400:
        return f"{int(seconds // 86400)}天前"
    return ""


def _gmail_full_date(value) -> str:
    """邮件详情日期（Gmail 中文）：「10月10日周五 下午8:31 (2小时前)」，往年带年份。"""
    if value is None:
        return ""
    weekday = _WEEKDAYS[value.weekday()]
    prefix = "" if value.year == datetime.now().year else f"{value.year}年"
    text = f"{prefix}{value.month}月{value.day}日周{weekday} {_cn_time(value)}"
    ago = _relative_ago(value)
    return f"{text} ({ago})" if ago else text


_WS_RE = re.compile(r"\s+")


def _snippet(value: str | None, length: int = 200) -> str:
    """列表中的正文摘要：合并空白，截断到 length 个字符。"""
    if not value:
        return ""
    text = _WS_RE.sub(" ", str(value)).strip()
    return text[:length]


def _recipients(value: str | None, me: str | None = None) -> str:
    """「发送至 我、李娜」：收件人显示名，账号本人显示为「我」。"""
    names = []
    me_lower = (me or "").lower()
    for name, addr in parse_list(value):
        if me_lower and addr.lower() == me_lower:
            names.append("我")
        else:
            names.append(name or addr.split("@")[0] or addr)
    return "、".join(names)


templates.env.filters["avatar_tone"] = _avatar_tone
templates.env.filters["recipients"] = _recipients


def _addr_list(value: str | None) -> str:
    """完整地址列表「名字 <邮箱>」；名字为空或与邮箱相同时只显示邮箱。"""
    items = [format_address(name, addr) for name, addr in parse_list(value)]
    return ", ".join(i for i in items if i) or (value or "")


templates.env.filters["addr_list"] = _addr_list
templates.env.filters["gmail_date"] = _gmail_list_date
templates.env.filters["gmail_full_date"] = _gmail_full_date
templates.env.filters["snippet"] = _snippet
templates.env.globals["icon"] = _icon
templates.env.globals["sidebar_data"] = sidebar_data
templates.env.filters["addr_name"] = _addr_name
templates.env.filters["addr_email"] = _addr_email
templates.env.filters["fmt_dt"] = _fmt_dt
templates.env.filters["utc_to_local"] = utc_to_local
templates.env.filters["fmt_rel_dt"] = _fmt_rel_dt
templates.env.filters["shorten"] = _truncate
templates.env.filters["initials"] = _initials
templates.env.globals["web_auth_enabled"] = WEB_AUTH_ENABLED
templates.env.globals["realtime_enabled"] = REALTIME_ENABLED
templates.env.globals["sync_junk_enabled"] = SYNC_JUNK_ENABLED
templates.env.globals["static_version"] = _static_version()
templates.env.globals["csrf_input"] = csrf_input
templates.env.globals["csrf_token"] = csrf_token
