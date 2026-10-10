"""Telegram bot integration: outbound notifications + low-level Bot API.

Best-effort, dependency-free (stdlib ``urllib``). New inbox mail persisted by
:func:`safe_sync_account` triggers a formatted push with inline action buttons
(一键已读/删除). The long-polling listener in :mod:`telegram_bot` consumes the
same low-level helpers defined here. Any failure is logged and swallowed so
mail sync is never affected.
"""
from __future__ import annotations

import json
import logging
import mimetypes
import os
import uuid
import urllib.error
import urllib.request
from datetime import datetime
from html import escape
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from ..config import (
    TELEGRAM_API_TIMEOUT_SEC,
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_IDS,
    TELEGRAM_NOTIFY_ENABLED,
    TELEGRAM_NOTIFY_SENT,
)
from ..database import session_scope
from ..models import Account, Message

logger = logging.getLogger(__name__)

_API_BASE = "https://api.telegram.org"

# Detail at most this many messages per notification before collapsing.
_MAX_DETAIL = 20
# When a sync brings in this many or fewer inbox messages, send one actionable
# message each (with buttons). Above this, send a single compact summary.
_MAX_ACTIONABLE = 20
_SUBJECT_MAX = 120
_FROM_MAX = 80
_TG_TEXT_LIMIT = 3900
_ATTACHMENT_MAX_PER_MESSAGE = 8


def is_configured() -> bool:
    """True when both a bot token and at least one chat id are set."""
    return bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_IDS)


def is_enabled() -> bool:
    """True when configured *and* notifications are turned on."""
    return is_configured() and TELEGRAM_NOTIFY_ENABLED


def _shorten(value: Optional[str], length: int) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    return text if len(text) <= length else text[: length - 1] + "\u2026"


def api_call(method: str, payload: dict[str, Any], *, timeout: Optional[int] = None) -> tuple[bool, Any]:
    """Call a Bot API ``method``. Returns ``(ok, result_or_error_string)``."""
    if not TELEGRAM_BOT_TOKEN:
        return False, "未配置 TELEGRAM_BOT_TOKEN"

    url = f"{_API_BASE}/bot{TELEGRAM_BOT_TOKEN}/{method}"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout or TELEGRAM_API_TIMEOUT_SEC) as resp:
            body = resp.read().decode("utf-8", "replace")
        parsed = json.loads(body or "{}")
        if parsed.get("ok"):
            return True, parsed.get("result")
        return False, str(parsed.get("description") or body or "未知错误")
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            raw = exc.read().decode("utf-8", "replace")
            detail = str(json.loads(raw).get("description") or raw)
        except Exception:
            detail = str(exc)
        return False, f"HTTP {exc.code}: {detail}"
    except Exception as exc:  # network / DNS / timeout
        return False, str(exc)


def send_message(
    text: str,
    *,
    chat_id: Optional[str] = None,
    reply_markup: Optional[dict] = None,
) -> tuple[bool, str]:
    """Send an HTML message. With no ``chat_id``, broadcasts to all configured
    chats. Returns ``(ok, detail)`` where ``ok`` is True if at least one send
    succeeded."""
    targets = [chat_id] if chat_id else list(TELEGRAM_CHAT_IDS)
    if not targets:
        return False, "未配置 TELEGRAM_CHAT_ID"

    any_ok = False
    last_detail = "ok"
    for target in targets:
        payload: dict[str, Any] = {
            "chat_id": target,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        ok, detail = api_call("sendMessage", payload)
        any_ok = any_ok or ok
        if not ok:
            last_detail = str(detail)
            logger.warning("Telegram sendMessage to %s failed: %s", target, detail)
    return any_ok, ("ok" if any_ok else last_detail)


def _build_multipart_body(
    *,
    fields: dict[str, str],
    file_field: str,
    file_name: str,
    file_bytes: bytes,
    file_content_type: str,
) -> tuple[bytes, str]:
    boundary = f"----cursor-{uuid.uuid4().hex}"
    chunks: list[bytes] = []
    for key, value in fields.items():
        chunks.extend(
            [
                f"--{boundary}\r\n".encode("utf-8"),
                f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode("utf-8"),
                str(value).encode("utf-8"),
                b"\r\n",
            ]
        )
    chunks.extend(
        [
            f"--{boundary}\r\n".encode("utf-8"),
            (
                f'Content-Disposition: form-data; name="{file_field}"; '
                f'filename="{file_name}"\r\n'
            ).encode("utf-8"),
            f"Content-Type: {file_content_type}\r\n\r\n".encode("utf-8"),
            file_bytes,
            b"\r\n",
            f"--{boundary}--\r\n".encode("utf-8"),
        ]
    )
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def send_document(
    file_path: str,
    *,
    filename: Optional[str] = None,
    caption: Optional[str] = None,
    chat_id: Optional[str] = None,
) -> tuple[bool, str]:
    """Upload one document so Telegram users can download attachments."""
    targets = [chat_id] if chat_id else list(TELEGRAM_CHAT_IDS)
    if not targets:
        return False, "未配置 TELEGRAM_CHAT_ID"
    if not TELEGRAM_BOT_TOKEN:
        return False, "未配置 TELEGRAM_BOT_TOKEN"
    if not os.path.isfile(file_path):
        return False, "文件不存在"

    try:
        with open(file_path, "rb") as f:
            content = f.read()
    except OSError as exc:
        return False, str(exc)

    upload_name = (filename or os.path.basename(file_path) or "attachment").strip()
    content_type = mimetypes.guess_type(upload_name)[0] or "application/octet-stream"
    any_ok = False
    last_detail = "ok"
    for target in targets:
        fields: dict[str, str] = {"chat_id": str(target)}
        if caption:
            fields["caption"] = caption
            fields["parse_mode"] = "HTML"
        body, content_type_header = _build_multipart_body(
            fields=fields,
            file_field="document",
            file_name=upload_name,
            file_bytes=content,
            file_content_type=content_type,
        )
        url = f"{_API_BASE}/bot{TELEGRAM_BOT_TOKEN}/sendDocument"
        req = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": content_type_header},
        )
        try:
            with urllib.request.urlopen(req, timeout=TELEGRAM_API_TIMEOUT_SEC) as resp:
                raw = resp.read().decode("utf-8", "replace")
            parsed = json.loads(raw or "{}")
            ok = bool(parsed.get("ok"))
            detail = parsed.get("result") if ok else parsed.get("description") or raw
        except urllib.error.HTTPError as exc:
            ok = False
            try:
                detail = exc.read().decode("utf-8", "replace")
            except Exception:
                detail = str(exc)
        except Exception as exc:
            ok = False
            detail = str(exc)

        any_ok = any_ok or ok
        if not ok:
            last_detail = str(detail)
            logger.warning("Telegram sendDocument to %s failed: %s", target, detail)
    return any_ok, ("ok" if any_ok else last_detail)


def answer_callback_query(callback_query_id: str, text: str = "", *, show_alert: bool = False) -> None:
    payload: dict[str, Any] = {"callback_query_id": callback_query_id, "show_alert": show_alert}
    if text:
        payload["text"] = text
    api_call("answerCallbackQuery", payload)


def edit_reply_markup(chat_id: Any, message_id: int, reply_markup: Optional[dict] = None) -> None:
    """Replace (or clear) the inline keyboard of an already-sent message."""
    payload: dict[str, Any] = {"chat_id": chat_id, "message_id": message_id}
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    api_call("editMessageReplyMarkup", payload)


def get_updates(offset: int, timeout: int) -> tuple[bool, Any]:
    """Long-poll for updates. Network timeout is the poll timeout + headroom."""
    payload = {
        "offset": offset,
        "timeout": timeout,
        "allowed_updates": ["message", "callback_query"],
    }
    return api_call("getUpdates", payload, timeout=timeout + 10)


def get_me() -> tuple[bool, Any]:
    return api_call("getMe", {})


def send_test_message() -> tuple[bool, str]:
    """Send a fixed test message to all configured chats."""
    return send_message(
        "\u2705 <b>Telegram 通知已连接</b>\n"
        "这是一条来自 Gmail 客户端的测试消息。\n"
        "发送 /help 查看可用命令。"
    )


def _action_keyboard(message_id: int) -> dict:
    return {
        "inline_keyboard": [
            [
                {"text": "\u2713 标记已读", "callback_data": f"read:{message_id}"},
                {"text": "\U0001F5D1 删除", "callback_data": f"del:{message_id}"},
            ]
        ]
    }


def _format_message_line(msg: Message) -> str:
    subject = escape(_shorten(msg.subject, _SUBJECT_MAX)) or "<i>(无主题)</i>"
    sender = escape(_shorten(msg.from_addr, _FROM_MAX)) or "未知发件人"
    return f"\u2022 <b>{subject}</b>\n  \u2709 {sender}"


def _msg_datetime(dt: Optional[datetime]) -> str:
    if not dt:
        return "-"
    try:
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return "-"


def _full_body(msg: Message) -> str:
    text = (msg.body_text or "").strip()
    if text:
        return text
    html = (msg.body_html or "").strip()
    if not html:
        return ""
    # Keep this lightweight: convert obvious line breaks then strip tags.
    plain = (
        html.replace("<br>", "\n")
        .replace("<br/>", "\n")
        .replace("<br />", "\n")
        .replace("</p>", "\n")
        .replace("</div>", "\n")
    )
    import re

    plain = re.sub(r"<[^>]+>", "", plain)
    plain = re.sub(r"\n{3,}", "\n\n", plain)
    return plain.strip()


def _split_text(text: str, limit: int) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    cursor = 0
    while cursor < len(text):
        end = min(cursor + limit, len(text))
        if end < len(text):
            cut = text.rfind("\n", cursor, end)
            if cut > cursor + 100:
                end = cut
        part = text[cursor:end].strip()
        if part:
            chunks.append(part)
        cursor = end
    return chunks or [text[:limit]]


def _send_message_chunked(
    text: str,
    *,
    chat_id: Optional[str] = None,
    reply_markup: Optional[dict] = None,
) -> None:
    pieces = _split_text(text, _TG_TEXT_LIMIT)
    if not pieces:
        return
    for idx, piece in enumerate(pieces):
        suffix = "" if len(pieces) == 1 else f"\n\n<i>({idx + 1}/{len(pieces)})</i>"
        send_message(
            piece + suffix,
            chat_id=chat_id,
            reply_markup=(reply_markup if idx == 0 else None),
        )


def _notify_message_detail(msg: Message, safe_email: str) -> None:
    subject = escape(msg.subject or "(无主题)")
    sender = escape(msg.from_addr or "-")
    to_addr = escape(msg.to_addr or "-")
    cc_addr = escape(msg.cc_addr or "-")
    sent_at = escape(_msg_datetime(msg.date))
    body = escape(_full_body(msg) or "(正文为空)")
    header = (
        "\U0001F4EC <b>新邮件</b>\n"
        f"\U0001F4E5 账号：<code>{safe_email}</code>\n"
        f"\U0001F3F7 主题：<b>{subject}</b>\n"
        f"\U0001F4E7 发件人：{sender}\n"
        f"\U0001F4E8 收件人：{to_addr}\n"
        f"\U0001F4CC 抄送：{cc_addr}\n"
        f"\U0001F552 时间：{sent_at}\n\n"
        "<b>正文</b>\n"
    )
    _send_message_chunked(
        header + body,
        reply_markup=_action_keyboard(int(msg.id)),
    )

    files = [a for a in (msg.attachments or []) if not a.is_inline]
    if not files:
        return
    send_message(f"\U0001F4CE 附件 {len(files)} 个，开始发送可下载文件…")
    for index, att in enumerate(files[:_ATTACHMENT_MAX_PER_MESSAGE], start=1):
        caption = (
            f"\U0001F4CE 附件 {index}/{min(len(files), _ATTACHMENT_MAX_PER_MESSAGE)}\n"
            f"<b>{escape(att.display_name or att.filename)}</b>"
        )
        ok, detail = send_document(
            att.file_path,
            filename=(att.display_name or att.filename),
            caption=caption,
        )
        if not ok:
            logger.warning("Telegram attachment send failed message_id=%s: %s", msg.id, detail)
            send_message(
                f"\u26A0 附件发送失败：<code>{escape(att.display_name or att.filename)}</code>"
            )
    if len(files) > _ATTACHMENT_MAX_PER_MESSAGE:
        send_message(
            f"\u2139 为避免刷屏，仅发送前 {_ATTACHMENT_MAX_PER_MESSAGE} 个附件。"
        )


def notify_new_mail(account_id: int, *, inbox: int = 0, sent: int = 0) -> None:
    """Push a Telegram summary for newly arrived mail on one account.

    Designed to run on the background pool. Honours the per-account
    ``notify_enabled`` flag and the global toggle.
    """
    if not is_enabled():
        return
    inbox = max(int(inbox or 0), 0)
    sent = max(int(sent or 0), 0)
    notify_sent = TELEGRAM_NOTIFY_SENT and sent > 0
    if inbox <= 0 and not notify_sent:
        return

    try:
        with session_scope() as db:
            account = db.get(Account, account_id)
            if account is not None and not bool(account.notify_enabled):
                return  # per-account notifications disabled
            email = account.email if account else f"#{account_id}"

            recent: list[Message] = []
            if inbox > 0:
                recent = db.execute(
                    select(Message)
                    .options(selectinload(Message.attachments))
                    .where(
                        Message.account_id == account_id,
                        Message.folder_kind == "inbox",
                    )
                    .order_by(Message.id.desc())
                    .limit(min(inbox, _MAX_DETAIL))
                ).scalars().all()
    except Exception:
        logger.debug("Failed to build Telegram notification", exc_info=True)
        return

    safe_email = escape(email)

    if inbox > 0 and recent:
        for msg in recent:
            _notify_message_detail(msg, safe_email)
        if inbox > len(recent):
            send_message(
                f"\u2139 本次共同步 {inbox} 封新邮件，已展示最近 {len(recent)} 封。"
            )

    if notify_sent:
        send_message(
            f"\u2197 <b>已发送</b> {sent} 封\n\U0001F4E5 账号：<code>{safe_email}</code>"
        )
