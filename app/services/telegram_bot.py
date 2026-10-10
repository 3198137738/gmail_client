"""Telegram bot long-polling listener.

Runs one daemon thread that polls ``getUpdates`` and reacts to:

* inline button presses on notifications - ``read:<id>`` / ``del:<id>``;
* text commands - ``/start``, ``/help``, ``/unread``, ``/status``.

Only chats listed in ``TELEGRAM_CHAT_IDS`` are honoured; everything else is
ignored. The loop is fully self-healing: any API/network error backs off and
retries without taking the process down. Disabled when commands are turned off
or the bot is not configured.
"""
from __future__ import annotations

import logging
import threading
from html import escape
from typing import Any, Optional

from ..config import (
    TELEGRAM_BOT_COMMANDS_ENABLED,
    TELEGRAM_CHAT_IDS,
    TELEGRAM_POLL_TIMEOUT_SEC,
)
from . import telegram_service as tg
from .message_actions import (
    count_unread,
    delete_by_id,
    list_unread,
    mark_not_spam,
    mark_read_by_id,
)

logger = logging.getLogger(__name__)

_ERROR_BACKOFF_SEC = 15

_HELP_TEXT = (
    "\U0001F916 <b>Gmail 通知机器人</b>\n\n"
    "可用命令：\n"
    "/unread - 查看最近未读邮件\n"
    "/status - 查看未读总数\n"
    "/help - 显示本帮助\n\n"
    "收到新邮件时可直接点击消息下方按钮“标记已读 / 删除”。"
)


def _format_unread(counts: dict[str, int]) -> str:
    """例如：<b>5</b> 封（收件箱 3，垃圾邮件 2）。"""
    total = sum(counts.values())
    text = f"<b>{total}</b> 封"
    if counts.get("junk"):
        text += f"（收件箱 {counts.get('inbox', 0)}，垃圾邮件 {counts['junk']}）"
    return text


def _authorized() -> set[str]:
    return {str(c) for c in TELEGRAM_CHAT_IDS}


class TelegramBot(threading.Thread):
    def __init__(self) -> None:
        super().__init__(name="telegram-bot", daemon=True)
        self._stop = threading.Event()
        self._offset = 0

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        logger.info("Telegram bot listener started")
        while not self._stop.is_set():
            ok, result = tg.get_updates(self._offset, TELEGRAM_POLL_TIMEOUT_SEC)
            if not ok:
                logger.warning("Telegram getUpdates failed: %s", result)
                if self._stop.wait(_ERROR_BACKOFF_SEC):
                    break
                continue
            try:
                for update in result or []:
                    self._offset = int(update.get("update_id", 0)) + 1
                    self._handle_update(update)
            except Exception:
                logger.exception("Telegram update handling error")
        logger.info("Telegram bot listener stopped")

    # -- dispatch -----------------------------------------------------------

    def _handle_update(self, update: dict[str, Any]) -> None:
        if "callback_query" in update:
            self._handle_callback(update["callback_query"])
        elif "message" in update:
            self._handle_message(update["message"])

    def _handle_message(self, message: dict[str, Any]) -> None:
        chat_id = str(message.get("chat", {}).get("id", ""))
        if chat_id not in _authorized():
            return
        text = (message.get("text") or "").strip()
        if not text.startswith("/"):
            return
        command = text.split()[0].split("@", 1)[0].lower()

        if command in ("/start", "/help"):
            tg.send_message(_HELP_TEXT, chat_id=chat_id)
        elif command == "/status":
            tg.send_message(f"\U0001F4EC 当前未读邮件：{_format_unread(count_unread())}", chat_id=chat_id)
        elif command == "/unread":
            self._send_unread(chat_id)
        else:
            tg.send_message("未知命令，发送 /help 查看可用命令。", chat_id=chat_id)

    def _send_unread(self, chat_id: str) -> None:
        counts = count_unread()
        if sum(counts.values()) == 0:
            tg.send_message("\u2728 没有未读邮件。", chat_id=chat_id)
            return
        items = list_unread(limit=10)
        tg.send_message(
            f"\U0001F4EC 未读邮件 {_format_unread(counts)}，最近 {len(items)} 封：",
            chat_id=chat_id,
        )
        for item in items:
            subject = escape(item["subject"]) or "<i>(无主题)</i>"
            sender = escape(item["from_addr"]) or "未知发件人"
            account = escape(item["account"])
            tag = "\u26A0 <b>[垃圾邮件]</b> " if item.get("folder_kind") == "junk" else ""
            body = (
                f"\u2022 {tag}<b>{subject}</b>\n"
                f"  \u2709 {sender}\n"
                f"  \U0001F4E5 <code>{account}</code>"
            )
            tg.send_message(
                body,
                chat_id=chat_id,
                reply_markup=tg._action_keyboard(item["id"], junk=item.get("folder_kind") == "junk"),
            )

    def _handle_callback(self, cq: dict[str, Any]) -> None:
        cq_id = cq.get("id", "")
        message = cq.get("message", {}) or {}
        chat = message.get("chat", {}) or {}
        chat_id = str(chat.get("id", ""))
        message_id = message.get("message_id")
        data = cq.get("data") or ""

        if chat_id not in _authorized():
            tg.answer_callback_query(cq_id, "未授权", show_alert=True)
            return

        action, _, raw_id = data.partition(":")
        try:
            target_id = int(raw_id)
        except (TypeError, ValueError):
            tg.answer_callback_query(cq_id, "无效操作")
            return

        if action == "read":
            ok, info = mark_read_by_id(target_id)
            toast = "已标记为已读" if ok else f"失败：{info}"
            done_note = "\u2713 已标记已读"
        elif action == "del":
            ok, info = delete_by_id(target_id)
            toast = "已删除" if ok else f"失败：{info}"
            done_note = "\U0001F5D1 已删除"
        elif action == "notspam":
            ok, info = mark_not_spam(target_id)
            toast = info if ok else f"失败：{info}"
            done_note = "\u2705 已移回收件箱，并信任该发件人"
        else:
            tg.answer_callback_query(cq_id, "未知操作")
            return

        tg.answer_callback_query(cq_id, toast)
        if ok and message_id is not None:
            # Clear the buttons so the action can't be repeated.
            tg.edit_reply_markup(chat.get("id"), message_id, None)
            original = message.get("text") or ""
            new_text = f"{escape_existing(original)}\n\n<i>{done_note}</i>"
            tg.api_call(
                "editMessageText",
                {
                    "chat_id": chat.get("id"),
                    "message_id": message_id,
                    "text": new_text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                },
            )


def escape_existing(text: str) -> str:
    """Re-escape plain text pulled back from Telegram (it strips our HTML)."""
    return escape(text or "")


_bot: Optional[TelegramBot] = None
_lock = threading.Lock()


def start_bot() -> None:
    global _bot
    if not TELEGRAM_BOT_COMMANDS_ENABLED:
        logger.info("Telegram bot commands disabled (TELEGRAM_BOT_COMMANDS_ENABLED=false)")
        return
    if not tg.is_configured():
        logger.info("Telegram bot not configured; listener not started")
        return
    with _lock:
        if _bot is not None and _bot.is_alive():
            return
        _bot = TelegramBot()
        _bot.start()


def shutdown_bot() -> None:
    global _bot
    with _lock:
        bot = _bot
        _bot = None
    if bot is not None:
        bot.stop()
