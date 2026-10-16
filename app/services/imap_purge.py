"""删除某个发件人在 Gmail 上的全部邮件。

1. 「所有邮件」和「垃圾邮件」里该发件人的邮件 → 移到「已删除邮件」（屏蔽发件人时只做这一步，
   邮件出现在左侧「已删除」里，30 天后由 Gmail 自动清除）；
2. 「屏蔽的地址」页手动「永久删除」时再把「已删除邮件」里该发件人的邮件 ``\\Deleted`` + EXPUNGE
   （Gmail 中从垃圾箱删除即永久删除）。

IMAP 的 ``SEARCH FROM`` 是子串匹配（``a@b.com`` 也会命中 ``xa@b.com``），永久删除不能
容忍误伤，所以命中的每一封都再取 From 头精确核对。屏蔽记录本身保持不变。

屏蔽之后新到的邮件由同步发现后经 outbox 移到已删除邮件（见 imap_service._trash_blocked）。
"""
from __future__ import annotations

import logging
from typing import Optional

from imapclient import IMAPClient
from imapclient.exceptions import IMAPClientError
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from ..config import DEFAULT_FOLDER
from ..crypto import decrypt_password
from ..database import session_scope
from ..models import Account, Message
from .imap_client import (
    IMAPSyncError,
    _account_lock,
    _discover_junk_folder,
    _discover_special_folder,
    _discover_trash_folder,
    _imap_client,
    _release_account_lock,
)
from .imap_ops import _expunge_uids, _move_uids
from .imap_service import _normalize_sender_email
from .message_actions import delete_local_messages

logger = logging.getLogger(__name__)

# 一条 IMAP 命令里最多带多少个 UID（命令行过长会被服务器拒绝）
_UID_CHUNK = 500
_FROM_HEADER_KEY = b"BODY[HEADER.FIELDS (FROM)]"


def _chunks(items: list[int], size: int = _UID_CHUNK):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def _header_sender(data: dict) -> str:
    """从 FETCH 回来的 From 头里取出规范化的邮箱地址。"""
    raw = data.get(_FROM_HEADER_KEY) or b""
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "replace")
    text = str(raw).strip()
    if ":" in text:
        text = text.split(":", 1)[1]
    return _normalize_sender_email(" ".join(text.split()))


def _sender_uids(client: IMAPClient, folder: str, sender: str) -> list[int]:
    """``folder`` 中确实由 ``sender`` 发出的 UID（SEARCH 粗筛 + From 头精确核对）。"""
    client.select_folder(folder, readonly=False)
    raw = client.search(["FROM", sender])
    candidates = sorted({int(uid) for uid in raw if int(uid) > 0})
    if not candidates:
        return []
    matched: list[int] = []
    for chunk in _chunks(candidates):
        fetched = client.fetch(chunk, ["BODY.PEEK[HEADER.FIELDS (FROM)]"])
        for uid, data in fetched.items():
            if _header_sender(data) == sender:
                matched.append(int(uid))
    return sorted(matched)


def _purge_local(account_id: int, sender: str, *, permanent: bool) -> int:
    """删掉本地残留的该发件人邮件（屏蔽时已删大部分，这里兜底）。

    只移到已删除时，本地「已删除」里的保留（它们在 Gmail 里也还在）。
    """
    with session_scope() as db:
        stmt = (
            select(Message)
            .options(selectinload(Message.attachments))
            .where(Message.account_id == account_id, Message.from_addr.ilike(f"%{sender}%"))
        )
        if not permanent:
            stmt = stmt.where(Message.folder_kind != "trash")
        rows = db.execute(stmt).scalars().all()
        targets = [m for m in rows if _normalize_sender_email(m.from_addr) == sender]
        return delete_local_messages(db, targets)


def purge_sender_messages(
    account_id: int,
    sender_email: str,
    *,
    permanent: bool = True,
    lock_timeout: float = 30.0,
) -> dict:
    """删除该账号中某发件人的全部邮件；``permanent=False`` 时只移到已删除邮件。

    返回 ``{"ok": bool, "permanent": bool, "trashed": n, "purged": n, "local": n, "folders": {...},
    "error": str|None}``：``trashed`` 是从所有邮件 / 垃圾邮件移进垃圾箱的封数，``purged`` 是最终从
    垃圾箱彻底清除的封数（含之前就在垃圾箱里的；只移到已删除时为 0）。不抛异常。
    """
    sender = _normalize_sender_email(sender_email)
    result: dict = {
        "ok": True, "permanent": permanent, "trashed": 0, "purged": 0, "local": 0, "folders": {}, "error": None,
    }
    if not sender:
        return {**result, "ok": False, "error": "invalid_sender"}

    try:
        with session_scope() as db:
            account = db.get(Account, account_id)
            if account is None:
                return {**result, "ok": False, "error": "account_not_found"}
            email = account.email
            encrypted = account.encrypted_password
        password = decrypt_password(encrypted)
    except ValueError:
        return {**result, "ok": False, "error": "decrypt_failed"}
    except Exception:
        logger.exception("Failed to load account for purge id=%s", account_id)
        return {**result, "ok": False, "error": "db_load_failed"}

    lock = _account_lock(account_id)
    if not lock.acquire(blocking=True, timeout=lock_timeout):
        logger.warning("Timed out acquiring lock for purge account=%s", account_id)
        return {**result, "ok": False, "error": "busy"}
    try:
        with _imap_client(email, password) as client:
            trash = _discover_trash_folder(client)
            all_mail = _discover_special_folder(client, "\\All")
            junk = _discover_junk_folder(client)

            # 没有「所有邮件」（非 Gmail）时退回收件箱
            sources: list[str] = []
            for folder in (all_mail or DEFAULT_FOLDER, junk):
                if folder and folder != trash and folder not in sources:
                    sources.append(folder)

            for folder in sources:
                try:
                    uids = _sender_uids(client, folder, sender)
                    for chunk in _chunks(uids):
                        if trash:
                            _move_uids(client, chunk, trash)
                        else:
                            _expunge_uids(client, chunk)
                    result["folders"][folder] = {"count": len(uids), "error": None}
                    result["trashed"] += len(uids)
                except IMAPClientError as exc:
                    result["ok"] = False
                    result["folders"][folder] = {"count": 0, "error": str(exc)}
                    logger.warning("Purge failed account=%s folder=%s sender=%s: %s", account_id, folder, sender, exc)

            if trash and permanent:
                try:
                    uids = _sender_uids(client, trash, sender)
                    for chunk in _chunks(uids):
                        _expunge_uids(client, chunk)
                    result["folders"][trash] = {"count": len(uids), "error": None}
                    result["purged"] = len(uids)
                except IMAPClientError as exc:
                    result["ok"] = False
                    result["folders"][trash] = {"count": 0, "error": str(exc)}
                    logger.warning("Purge expunge failed account=%s sender=%s: %s", account_id, sender, exc)
            elif not trash:
                # 没有垃圾箱：上面已经直接 EXPUNGE
                result["purged"] = result["trashed"]
    except IMAPSyncError as exc:
        return {**result, "ok": False, "error": str(exc)}
    except Exception as exc:
        logger.exception("Unexpected purge_sender_messages failure account=%s", account_id)
        return {**result, "ok": False, "error": exc.__class__.__name__}
    finally:
        _release_account_lock(account_id, lock)

    try:
        result["local"] = _purge_local(account_id, sender, permanent=permanent)
    except Exception:
        logger.exception("Local purge failed account=%s sender=%s", account_id, sender)

    if not result["ok"] and not result["error"]:
        result["error"] = next(
            (f["error"] for f in result["folders"].values() if f.get("error")), "unknown"
        )
    logger.info(
        "Purged sender account=%s sender=%s permanent=%s trashed=%s purged=%s local=%s ok=%s",
        account_id, sender, permanent, result["trashed"], result["purged"], result["local"], result["ok"],
    )
    return result


_ERROR_TEXT = {
    "busy": "账号正在同步，请稍后再试",
    "account_not_found": "账号不存在",
    "decrypt_failed": "无法解密账号密码",
    "invalid_sender": "发件人地址无效",
}


def describe_purge(result: dict) -> str:
    """给页面提示 / 审计日志用的一句话。"""
    error: Optional[str] = result.get("error")
    if error and not result.get("folders"):
        # 还没碰到任何文件夹就失败了（连不上、锁被占用等）
        return _ERROR_TEXT.get(error, f"连接 Gmail 失败：{error}")
    if result.get("permanent", True):
        text = f"已永久删除 {int(result.get('purged') or 0)} 封"
    else:
        text = f"已移到已删除 {int(result.get('trashed') or 0)} 封"
    if not result.get("ok"):
        text += f"，部分文件夹操作失败：{error}"
    return text
