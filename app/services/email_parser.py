"""Robust MIME parser that extracts headers, bodies, inline images and attachments.

Inline images:
* multipart/related parts carrying a ``Content-ID`` header are stored under
  ``attachments/{account_id}/{folder_kind}/{uid}/inline/`` and registered
  with a public URL.
* The HTML body's ``cid:`` references (with or without angle brackets) are
  rewritten to those local URLs prior to sanitization.

Regular attachments live under
``attachments/{account_id}/{folder_kind}/{uid}/files/``.
"""
from __future__ import annotations

import logging
import mimetypes
import re
from dataclasses import dataclass, field
from datetime import datetime
from email import message_from_bytes, policy
from email.header import decode_header, make_header
from email.message import Message
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Iterable, List, Optional

from ..config import ATTACHMENTS_DIR, ATTACHMENTS_URL_PREFIX
from ..addresses import format_address, parse_list
from .html_sanitizer import sanitize_html, text_to_safe_html

logger = logging.getLogger(__name__)


@dataclass
class ParsedAttachment:
    filename: str
    mime_type: str
    is_inline: bool
    content_id: Optional[str]
    file_path: str
    public_url: str
    size: int
    # Original, possibly non-ASCII filename from the email. Used for display
    # and for the download Content-Disposition; ``filename`` stays ASCII-safe
    # for the filesystem and URLs.
    display_name: Optional[str] = None


@dataclass
class ParsedMessage:
    subject: Optional[str] = None
    from_addr: Optional[str] = None
    to_addr: Optional[str] = None
    cc_addr: Optional[str] = None
    bcc_addr: Optional[str] = None
    date: Optional[datetime] = None
    message_id: Optional[str] = None
    # 会话的第一封邮件的 Message-ID（References 的第一项，没有时取 In-Reply-To、再取自身），
    # 服务器不提供会话 id（非 Gmail）时据此把往来邮件归为同一会话
    thread_root: Optional[str] = None
    body_text: Optional[str] = None
    body_html: Optional[str] = None
    has_attachments: bool = False
    attachments: List[ParsedAttachment] = field(default_factory=list)


_SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._\-]+")
_EXTENSION_RE = re.compile(r"\.([A-Za-z0-9]{1,10})$")


def _safe_filename(name: str, fallback: str) -> str:
    """Return a filesystem-safe filename, falling back when empty.

    扩展名单独保留：像「十月发票.pdf」这样主体全是非 ASCII 字符的文件名，
    主体部分用 fallback 的主体代替，仍保留 .pdf，而不是只剩下 "pdf"。
    """
    if not name:
        return fallback
    ext_match = _EXTENSION_RE.search(name)
    ext = ext_match.group(1) if ext_match else ""
    stem = name[: ext_match.start()] if ext_match else name
    clean_stem = _SAFE_FILENAME_RE.sub("_", stem).strip("._-")
    if not clean_stem:
        if not ext:
            return fallback
        clean_stem = fallback.rsplit(".", 1)[0] if "." in fallback else fallback
    cleaned = f"{clean_stem}.{ext}" if ext else clean_stem
    return cleaned[-200:] if len(cleaned) > 200 else cleaned


def _decode_header_value(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        logger.warning("Failed to decode header (length=%d)", len(value or ""))
        return value


def _format_addresses(raw_value: Optional[str]) -> Optional[str]:
    if not raw_value:
        return None
    try:
        pairs = parse_list(raw_value)
    except Exception:
        return _decode_header_value(raw_value)
    formatted = []
    for name, addr in pairs:
        name_decoded = _decode_header_value(name) or ""
        item = format_address(name_decoded, addr)
        if item:
            formatted.append(item)
    return ", ".join(formatted) if formatted else None


def _parse_date(raw_value: Optional[str]) -> Optional[datetime]:
    if not raw_value:
        return None
    try:
        dt = parsedate_to_datetime(raw_value)
        if dt is None:
            return None
        if dt.tzinfo is not None:
            dt = dt.astimezone(tz=None).replace(tzinfo=None)
        return dt
    except (TypeError, ValueError):
        return None


def _decode_part_text(part: Message) -> str:
    """Decode a text/* part using its declared charset, with safe fallbacks."""
    payload = part.get_payload(decode=True)
    if payload is None:
        return ""
    charset = part.get_content_charset() or "utf-8"
    for enc in (charset, "utf-8", "latin-1"):
        try:
            return payload.decode(enc, errors="strict")
        except (UnicodeDecodeError, LookupError):
            continue
    return payload.decode("utf-8", errors="replace")


def _normalize_cid(raw_cid: Optional[str]) -> Optional[str]:
    if not raw_cid:
        return None
    cid = raw_cid.strip()
    if cid.startswith("<") and cid.endswith(">"):
        cid = cid[1:-1]
    return cid or None


def _is_inline_part(part: Message) -> bool:
    """A part is inline if disposition='inline' OR a Content-ID exists."""
    disposition = (part.get("Content-Disposition") or "").lower()
    if "inline" in disposition:
        return True
    if part.get("Content-ID"):
        return True
    return False


def _guess_extension(mime_type: str) -> str:
    ext = mimetypes.guess_extension(mime_type or "") or ""
    if not ext and mime_type:
        sub = mime_type.split("/")[-1]
        if sub and re.fullmatch(r"[A-Za-z0-9.+-]+", sub):
            ext = "." + sub.split("+")[0]
    return ext or ".bin"


def _replace_cid_references(html_text: str, cid_to_url: dict[str, str]) -> str:
    """Rewrite cid: references to local URLs, tolerant of quoting variations."""
    if not html_text or not cid_to_url:
        return html_text

    def _replace(match: re.Match) -> str:
        cid = match.group("cid").strip()
        cid = cid[1:-1] if cid.startswith("<") and cid.endswith(">") else cid
        url = cid_to_url.get(cid)
        if not url:
            return match.group(0)
        return f'{match.group("prefix")}{url}{match.group("suffix")}'

    pattern = re.compile(
        r"""(?P<prefix>src\s*=\s*['"]?)cid:(?P<cid><?[^'"<>\s)]+>?)(?P<suffix>['"]?)""",
        re.IGNORECASE,
    )
    return pattern.sub(_replace, html_text)


def _message_base_dir(account_id: int, folder_kind: str, uid: int) -> Path:
    return ATTACHMENTS_DIR / str(account_id) / folder_kind / str(uid)


def _walk_parts(msg: Message) -> Iterable[Message]:
    for part in msg.walk():
        if part.is_multipart():
            continue
        yield part


_MSGID_RE = re.compile(r"<([^<>\s]+)>")


def _thread_root(msg, message_id: Optional[str]) -> Optional[str]:
    for header in ("References", "In-Reply-To"):
        try:
            found = _MSGID_RE.findall(str(msg.get(header) or ""))
        except Exception:
            found = []
        if found:
            return found[0].lower()
    own = _MSGID_RE.findall(message_id or "")
    return own[0].lower() if own else None


def parse_email(
    raw_bytes: bytes,
    account_id: int,
    uid: int,
    folder_kind: str = "inbox",
) -> ParsedMessage:
    """Parse a raw RFC822 byte stream into a :class:`ParsedMessage`."""
    msg = message_from_bytes(raw_bytes, policy=policy.default)

    parsed = ParsedMessage(
        subject=_decode_header_value(msg.get("Subject")),
        from_addr=_format_addresses(msg.get("From")),
        to_addr=_format_addresses(msg.get("To")),
        cc_addr=_format_addresses(msg.get("Cc")),
        bcc_addr=_format_addresses(msg.get("Bcc")),
        date=_parse_date(msg.get("Date")),
        message_id=(msg.get("Message-ID") or "").strip() or None,
    )
    parsed.thread_root = _thread_root(msg, parsed.message_id)

    text_chunks: list[str] = []
    html_chunks: list[str] = []
    cid_to_url: dict[str, str] = {}
    inline_seq = 0
    file_seq = 0
    base_dir = _message_base_dir(account_id, folder_kind, uid)
    inline_dir: Optional[Path] = None
    files_dir: Optional[Path] = None

    for part in _walk_parts(msg):
        ctype = (part.get_content_type() or "").lower()
        disposition = (part.get("Content-Disposition") or "").lower()
        is_attachment_disposition = "attachment" in disposition
        is_inline = _is_inline_part(part)

        if ctype == "text/plain" and not is_attachment_disposition:
            text_chunks.append(_decode_part_text(part))
            continue

        if ctype == "text/html" and not is_attachment_disposition:
            html_chunks.append(_decode_part_text(part))
            continue

        payload = part.get_payload(decode=True)
        if payload is None:
            continue

        raw_filename = part.get_filename()
        filename = _decode_header_value(raw_filename) if raw_filename else None
        cid = _normalize_cid(part.get("Content-ID"))

        treat_as_inline = is_inline and ctype.startswith("image/") and (cid or not is_attachment_disposition)

        if treat_as_inline:
            inline_seq += 1
            ext = _guess_extension(ctype)
            safe_name = _safe_filename(filename or "", f"inline_{inline_seq}{ext}")
            if "." not in safe_name:
                safe_name = f"{safe_name}{ext}"
            if inline_dir is None:
                inline_dir = base_dir / "inline"
                inline_dir.mkdir(parents=True, exist_ok=True)
            target = inline_dir / safe_name
            target.write_bytes(payload)
            public_url = (
                f"{ATTACHMENTS_URL_PREFIX}/{account_id}/{folder_kind}/{uid}/inline/{safe_name}"
            )
            if cid:
                cid_to_url[cid] = public_url
            parsed.attachments.append(
                ParsedAttachment(
                    filename=safe_name,
                    mime_type=ctype or "application/octet-stream",
                    is_inline=True,
                    content_id=cid,
                    file_path=str(target),
                    public_url=public_url,
                    size=len(payload),
                    display_name=(filename or safe_name),
                )
            )
            continue

        file_seq += 1
        ext = _guess_extension(ctype)
        safe_name = _safe_filename(filename or "", f"attachment_{file_seq}{ext}")
        if files_dir is None:
            files_dir = base_dir / "files"
            files_dir.mkdir(parents=True, exist_ok=True)
        target = files_dir / safe_name
        n = 1
        while target.exists():
            stem = target.stem
            target = files_dir / f"{stem}_{n}{target.suffix}"
            n += 1
        target.write_bytes(payload)
        public_url = (
            f"{ATTACHMENTS_URL_PREFIX}/{account_id}/{folder_kind}/{uid}/files/{target.name}"
        )
        parsed.attachments.append(
            ParsedAttachment(
                filename=target.name,
                mime_type=ctype or "application/octet-stream",
                is_inline=False,
                content_id=cid,
                file_path=str(target),
                public_url=public_url,
                size=len(payload),
                display_name=(filename or target.name),
            )
        )

    body_text = "\n".join(text_chunks).strip() or None
    raw_html = "\n".join(html_chunks).strip() or None

    if raw_html:
        replaced = _replace_cid_references(raw_html, cid_to_url)
        parsed.body_html = sanitize_html(replaced)
    elif body_text:
        parsed.body_html = text_to_safe_html(body_text)

    parsed.body_text = body_text
    parsed.has_attachments = any(not a.is_inline for a in parsed.attachments)
    return parsed
