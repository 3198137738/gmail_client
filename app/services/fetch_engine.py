"""新邮件下载引擎（C++ 扩展 ``app.native.imapfetch``）的 Python 封装。

IDLE 连接只负责听通知；收到新邮件推送后由本模块驱动引擎在**常驻的下载连接**上取邮件：

1. 一条 ``UID FETCH last+1:*`` 先取头部（发件人、主题、标记、大小），立刻入库并推送页面
   —— 邮件马上出现在列表里，正文标记为「下载中」；
2. 正文按大小分给主连接和若干副连接并发取，每取到一封就补齐正文、附件、验证码并通知页面刷新；
3. 全部正文到齐后再执行自动规则、发 Telegram 通知（通知里需要正文摘要和验证码）。

引擎不可用（扩展未编译、自检没过、登录失败、连接断开）时返回 ``None``，调用方退回
``imap_service.ingest_new_messages``（Python 快速路径），功能不受影响。凭据、OAuth 令牌、
文件夹名都由 Python 决定后交给引擎；引擎出错即丢弃，下次推送时重新创建（会拿新的令牌）。
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime
from time import perf_counter
from typing import Any, Optional

from imapclient import imap_utf7
from imapclient.datetime_util import parse_to_datetime

from ..config import (
    FETCH_ENGINE_ENABLED,
    FETCH_ENGINE_EXTRA_CONNECTIONS,
    FETCH_ENGINE_IDLE_CLOSE_SEC,
    FETCH_ENGINE_SMALL_BODY_KB,
    IDLE_FAST_PATH_MAX_MESSAGES,
    IMAP_COMPRESSION,
    IMAP_TIMEOUT_SEC,
)
from ..database import session_scope
from . import image_proxy, mail_providers
from .background import submit_bg
from .events import new_mail_preview, publish_mail_changed, publish_new_mail
from .imap_client import _FOLDER_LOCK_TIMEOUT_SEC, _folder_lock
from .imap_service import (
    _get_or_create_sync_state,
    _ingest_uids,
    _move_rescued_to_inbox,
    _record_error,
    _update_sync_state,
    complete_message_body,
)
from .telegram_service import notify_new_mail

logger = logging.getLogger(__name__)

_engines: dict[tuple[int, str], "EngineHandle"] = {}
_lock = threading.Lock()
_disabled_reason: Optional[str] = None


def _module():
    from .. import native

    return getattr(native, "imapfetch", None)


def available() -> bool:
    return FETCH_ENGINE_ENABLED and _module() is not None and _disabled_reason is None


def disable(reason: str) -> None:
    """自检不通过或运行中发现引擎不可靠：本进程内不再使用。"""
    global _disabled_reason
    _disabled_reason = reason
    drop_engine()
    logger.warning("fetch engine: disabled (%s)", reason)


def startup_log() -> None:
    if not FETCH_ENGINE_ENABLED:
        logger.info("fetch engine: disabled by FETCH_ENGINE_ENABLED")
    elif _module() is None:
        logger.info("fetch engine: unavailable (扩展未编译，新邮件走 Python 快速路径)")
    elif _disabled_reason:
        logger.info("fetch engine: disabled (%s)", _disabled_reason)
    else:
        logger.info("fetch engine: native (extra_connections=%s)", FETCH_ENGINE_EXTRA_CONNECTIONS)


class EngineHandle:
    """一个账号 + 文件夹的常驻引擎及其创建时的凭据（凭据变化时重建）。"""

    def __init__(self, engine, email: str, secret: str, folder: str) -> None:
        self.engine = engine
        self.signature = (email, secret, folder)

    def has_capability(self, cap: str) -> bool:
        try:
            return bool(self.engine.has_capability(cap))
        except Exception:
            return False

    def close(self) -> None:
        try:
            self.engine.close()
        except Exception:
            pass


def get_engine(account_id: int, email: str, secret: str, folder: str) -> Optional[EngineHandle]:
    """取得（必要时创建）该账号该文件夹的常驻引擎；不可用时返回 None。"""
    if not available():
        return None
    key = (account_id, folder)
    with _lock:
        handle = _engines.get(key)
    if handle is not None and handle.signature == (email, secret, folder):
        return handle
    if handle is not None:
        drop_engine(account_id, folder)
    provider = mail_providers.for_secret(secret)
    try:
        token = mail_providers.access_token(email, secret)
    except mail_providers.OAuthError as exc:
        logger.info("fetch engine: no token for account=%s: %s", account_id, exc)
        return None
    module = _module()
    try:
        engine = module.Engine(
            host=provider.imap_host,
            port=provider.imap_port,
            user=email,
            password="" if token else secret,
            token=token or "",
            # IMAP 文件夹名用修改版 UTF-7 传输（imapclient 平时替我们做了）
            folder=imap_utf7.encode(folder).decode("ascii"),
            extra_connections=FETCH_ENGINE_EXTRA_CONNECTIONS,
            small_body_bytes=FETCH_ENGINE_SMALL_BODY_KB * 1024,
            idle_close_sec=FETCH_ENGINE_IDLE_CLOSE_SEC,
            compress=IMAP_COMPRESSION,
            timeout_sec=max(IMAP_TIMEOUT_SEC, 60),
        )
    except Exception as exc:
        logger.warning("fetch engine: failed to create engine account=%s: %s", account_id, exc)
        return None
    handle = EngineHandle(engine, email, secret, folder)
    with _lock:
        _engines[key] = handle
    return handle


def drop_engine(account_id: Optional[int] = None, folder: Optional[str] = None) -> None:
    """关闭并丢弃引擎（账号删除、凭据变化、出错后）。不传参数则全部丢弃。"""
    with _lock:
        keys = [
            k for k in _engines
            if (account_id is None or k[0] == account_id) and (folder is None or k[1] == folder)
        ]
        handles = [_engines.pop(k) for k in keys]
    for h in handles:
        h.close()


# ---- 把引擎返回的记录转成 _ingest_uids 的 prefetched 格式 --------------------------

def _internaldate(value) -> Optional[datetime]:
    if not value:
        return None
    try:
        return parse_to_datetime(value if isinstance(value, bytes) else str(value).encode(), normalise=True)
    except Exception:
        return None


def _as_fetch_data(rec: dict, *, pending: bool) -> dict:
    data = {
        b"RFC822": bytes(rec["header"]),
        b"FLAGS": tuple(bytes(f) if not isinstance(f, bytes) else f for f in (rec.get("flags") or ())),
        b"INTERNALDATE": _internaldate(rec.get("internaldate")),
        b"X-GM-LABELS": tuple(bytes(l) if not isinstance(l, bytes) else l for l in (rec.get("labels") or ())),
    }
    if rec.get("thrid") is not None:
        data[b"X-GM-THRID"] = int(rec["thrid"])
    if pending:
        data[b"BODY_PENDING"] = True
    return data


def fetch_new_mail(
    account_id: int,
    email: str,
    secret: str,
    folder: str,
    folder_kind: str,
    *,
    uidvalidity: int,
) -> Optional[dict]:
    """收到新邮件推送后用引擎取邮件。返回 ``{"saved": n, ...}``；需要退回 Python 路径时返回 None。"""
    handle = get_engine(account_id, email, secret, folder)
    if handle is None:
        return None
    lock = _folder_lock(account_id, folder)
    if not lock.acquire(timeout=_FOLDER_LOCK_TIMEOUT_SEC):
        logger.warning("Folder busy too long, skipped account=%s folder=%s", account_id, folder)
        return None
    try:
        return _fetch_locked(handle, account_id, folder, folder_kind, uidvalidity=uidvalidity)
    finally:
        lock.release()


def _fetch_locked(handle: EngineHandle, account_id: int, folder: str, folder_kind: str, *, uidvalidity: int) -> Optional[dict]:
    started = perf_counter()
    with session_scope() as db:
        state = _get_or_create_sync_state(db, account_id, folder)
        last_uid = int(state.last_uid or 0)
        prev_validity = state.uidvalidity
    if last_uid <= 0 or (prev_validity and uidvalidity and int(prev_validity) != int(uidvalidity)):
        return None

    timing: dict[str, float] = {}
    saved_uids: list[int] = []
    completed_ids: list[int] = []
    result = {"saved": 0, "scanned": 0, "folder": folder, "folder_kind": folder_kind}
    announced = {"inbox": 0}

    def on_headers(records: list) -> None:
        """头部到达：入库（正文待取）、推进 last_uid、推送页面。"""
        timing["headers"] = perf_counter() - started
        pre = {int(r["uid"]): _as_fetch_data(r, pending=True) for r in records if int(r["uid"]) > last_uid}
        result["scanned"] = len(pre)
        if not pre:
            return
        uids = sorted(pre)
        ingest = _ingest_uids(handle, account_id, folder, folder_kind, uids, apply_rules=False, prefetched=pre)
        result["saved"] = ingest.saved
        if ingest.failed_uids:
            safe_uid = ingest.failed_uids[0] - 1
            new_last_uid = safe_uid if safe_uid > last_uid else None
        else:
            new_last_uid = max(uids)
        _update_sync_state(account_id, folder, uidvalidity=uidvalidity, new_last_uid=new_last_uid)
        _move_rescued_to_inbox(account_id, folder, ingest.rescue_uids)
        saved_uids.extend(u for u in uids if u not in set(ingest.failed_uids))
        if ingest.saved:
            counts = {folder_kind: ingest.saved} if folder_kind in ("inbox", "sent", "junk") else {}
            if counts:
                announced.update(counts)
                try:
                    publish_new_mail(account_id, preview=new_mail_preview(account_id, counts.get("inbox", 0)), **counts)
                except Exception:
                    logger.debug("Failed to publish new_mail event", exc_info=True)
            else:
                publish_mail_changed(account_id, folder_kind)
        timing["headers_done"] = perf_counter() - started

    def on_body(uid: int, raw) -> None:
        """一封正文到达：补齐正文、附件、验证码，通知页面刷新。"""
        timing.setdefault("first_body", perf_counter() - started)
        try:
            mid = complete_message_body(account_id, folder, folder_kind, int(uid), bytes(raw))
        except Exception:
            logger.exception("Failed to complete message body account=%s uid=%s", account_id, uid)
            return
        if mid is not None:
            completed_ids.append(mid)
            publish_mail_changed(account_id, folder_kind)

    try:
        info = handle.engine.fetch_new(last_uid, IDLE_FAST_PATH_MAX_MESSAGES, on_headers, on_body)
    except Exception as exc:
        logger.warning("fetch engine failed account=%s folder=%s: %s; falling back", account_id, folder, exc, exc_info=True)
        _record_error(account_id, folder, f"fetch engine: {exc}")
        drop_engine(account_id, folder)
        return None if not saved_uids else result   # 头部已入库的不重复下载；正文由完整同步补
    if info.get("too_many"):
        return None

    if result["saved"]:
        if folder_kind in ("inbox", "junk"):
            from .rules import safe_apply_to_new

            safe_apply_to_new(account_id, folder, saved_uids)
        if announced.get("inbox") or folder_kind in ("sent", "junk"):
            counts = {k: v for k, v in announced.items() if v}
            if counts:
                submit_bg(notify_new_mail, account_id, **counts)
        if folder_kind == "inbox":
            try:
                image_proxy.schedule_prefetch(account_id, folder, saved_uids)
            except Exception:
                logger.exception("Failed to schedule image prefetch account=%s", account_id)
    logger.info(
        "Fetch engine account=%s folder=%s kind=%s new=%s saved=%s headers=%.2fs first_body=%.2fs "
        "all_bodies=%.2fs conns=%s compressed=%s",
        account_id, folder, folder_kind, result["scanned"], result["saved"],
        timing.get("headers", 0.0), timing.get("first_body", 0.0), perf_counter() - started,
        info.get("connections", 1), info.get("compressed", False),
    )
    return result
