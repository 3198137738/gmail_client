"""Shared Jinja2 environment setup."""
from __future__ import annotations

from datetime import datetime

from fastapi.templating import Jinja2Templates
from markupsafe import Markup

from .config import REALTIME_ENABLED, TEMPLATES_DIR, WEB_AUTH_ENABLED
from .services.html_sanitizer import sanitize_html

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _safe_html(value: str) -> Markup:
    """Render stored ``body_html`` through the current sanitizer.

    Re-running sanitisation on render is intentional: it lets us retroactively
    clean up legacy rows whose ``body_html`` was produced by an older, buggier
    version of the sanitizer (e.g. leaked CSS from ``<style>`` blocks).
    Idempotent thanks to the post-process anchor-tag guard.
    """
    return Markup(sanitize_html(value or ""))


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


templates.env.filters["safe_html"] = _safe_html
templates.env.filters["fmt_dt"] = _fmt_dt
templates.env.filters["fmt_rel_dt"] = _fmt_rel_dt
templates.env.filters["shorten"] = _truncate
templates.env.filters["initials"] = _initials
templates.env.globals["web_auth_enabled"] = WEB_AUTH_ENABLED
templates.env.globals["realtime_enabled"] = REALTIME_ENABLED
