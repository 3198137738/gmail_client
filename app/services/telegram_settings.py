"""Telegram 推送设置：网页「设置 › 通知」中保存的值优先，没保存过的项沿用 .env。

保存在 app_settings 表（键 telegram_settings，JSON）；机器人令牌用 Fernet 加密。
读取有内存缓存，保存后立即生效（机器人监听会按新设置重启）。
"""
from __future__ import annotations

import json
import logging
import re
import threading
from dataclasses import dataclass
from typing import Any, Optional

from .. import config
from ..crypto import decrypt_password, encrypt_password
from ..database import session_scope
from ..models import AppSetting

logger = logging.getLogger(__name__)

SETTINGS_KEY = "telegram_settings"
BOOL_FIELDS = ("notify_enabled", "notify_sent", "notify_junk", "commands_enabled")
_TOKEN_RE = re.compile(r"^\d{5,}:[A-Za-z0-9_-]{30,}$")
_CHAT_RE = re.compile(r"^(-?\d{1,20}|@[A-Za-z][A-Za-z0-9_]{3,})$")


@dataclass(frozen=True)
class TelegramSettings:
    bot_token: str
    chat_ids: tuple[str, ...]
    notify_enabled: bool
    notify_sent: bool
    notify_junk: bool
    commands_enabled: bool
    # 哪些项来自网页保存（其余来自 .env）
    overridden: frozenset[str] = frozenset()

    @property
    def configured(self) -> bool:
        return bool(self.bot_token and self.chat_ids)


_cache: Optional[TelegramSettings] = None
_lock = threading.Lock()


def _parse_chat_ids(value: str) -> tuple[str, ...]:
    return tuple(c.strip() for c in re.split(r"[,，\s]+", value or "") if c.strip())


def _env_defaults() -> dict[str, Any]:
    return {
        "bot_token": config.TELEGRAM_BOT_TOKEN,
        "chat_ids": tuple(config.TELEGRAM_CHAT_IDS),
        # .env 没写 TELEGRAM_NOTIFY_ENABLED 时，配置了就默认开启
        "notify_enabled": config.TELEGRAM_NOTIFY_ENABLED,
        "notify_sent": config.TELEGRAM_NOTIFY_SENT,
        "notify_junk": config.TELEGRAM_NOTIFY_JUNK,
        "commands_enabled": config.TELEGRAM_BOT_COMMANDS_ENABLED,
    }


def _load_stored() -> dict[str, Any]:
    try:
        with session_scope() as db:
            row = db.get(AppSetting, SETTINGS_KEY)
            raw = row.value if row else None
    except Exception:  # 数据库还没初始化等：只用 .env
        logger.exception("Failed to load Telegram settings")
        return {}
    if not raw:
        return {}
    try:
        stored = json.loads(raw)
    except ValueError:
        return {}
    return stored if isinstance(stored, dict) else {}


def _build(stored: dict[str, Any]) -> TelegramSettings:
    values = _env_defaults()
    overridden = set()
    token = stored.get("bot_token")
    if token:
        try:
            values["bot_token"] = decrypt_password(token)
            overridden.add("bot_token")
        except ValueError:
            logger.warning("Stored Telegram bot token cannot be decrypted; using .env")
    if "chat_ids" in stored:
        values["chat_ids"] = tuple(str(c) for c in stored["chat_ids"] or ())
        overridden.add("chat_ids")
    for name in BOOL_FIELDS:
        if name in stored:
            values[name] = bool(stored[name])
            overridden.add(name)
    if "notify_enabled" not in stored and not config.TELEGRAM_NOTIFY_ENABLED_SET:
        # .env 没指定开关时：网页配置好了令牌和 Chat ID 也视为开启
        values["notify_enabled"] = bool(values["bot_token"] and values["chat_ids"])
    return TelegramSettings(**values, overridden=frozenset(overridden))


def current() -> TelegramSettings:
    global _cache
    cached = _cache
    if cached is not None:
        return cached
    with _lock:
        if _cache is None:
            _cache = _build(_load_stored())
        return _cache


def invalidate() -> None:
    global _cache
    with _lock:
        _cache = None


def validate_token(token: str) -> str:
    if not _TOKEN_RE.match(token):
        return "机器人令牌格式不对，应类似 123456789:AAH…（在 @BotFather 中获取）"
    return ""


def validate_chat_ids(ids: tuple[str, ...]) -> str:
    for c in ids:
        if not _CHAT_RE.match(c):
            return f"Chat ID「{c}」格式不对：应为数字（群组 / 频道以 -100 开头）或 @频道用户名"
    return ""


def save(changes: dict[str, Any]) -> TelegramSettings:
    """保存网页修改的项。bot_token 为 "" 表示不修改；clear_token=True 清除网页保存的令牌（回到 .env）。
    reset=True 清除全部网页设置。校验失败抛 ValueError。"""
    with session_scope() as db:
        row = db.get(AppSetting, SETTINGS_KEY)
        try:
            stored = json.loads(row.value) if row and row.value else {}
        except ValueError:
            stored = {}
        if changes.get("reset"):
            stored = {}
        else:
            token = (changes.get("bot_token") or "").strip()
            if token:
                error = validate_token(token)
                if error:
                    raise ValueError(error)
                stored["bot_token"] = encrypt_password(token)
            elif changes.get("clear_token"):
                stored.pop("bot_token", None)
            if changes.get("chat_ids") is not None:
                ids = _parse_chat_ids(str(changes["chat_ids"]))
                error = validate_chat_ids(ids)
                if error:
                    raise ValueError(error)
                stored["chat_ids"] = list(ids)
            for name in BOOL_FIELDS:
                if changes.get(name) is not None:
                    stored[name] = bool(changes[name])
        value = json.dumps(stored, ensure_ascii=False)
        if row is None:
            db.add(AppSetting(key=SETTINGS_KEY, value=value))
        else:
            row.value = value
    invalidate()
    return current()


def masked_token(token: str) -> str:
    """只显示令牌的机器人 ID 和末 4 位。"""
    if not token:
        return ""
    bot_id, _, secret = token.partition(":")
    return f"{bot_id}:…{secret[-4:]}" if secret else "…"
