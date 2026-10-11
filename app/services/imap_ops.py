"""对已入库邮件的 IMAP 远程操作：修改标记（已读/未读）、删除、移动。

都是「尽力而为」：本地数据库已先行更新，远程失败只记日志，不回滚。
与同步共用账号锁（见 imap_client），避免同一账号上的操作并发。
"""
from __future__ import annotations

import logging
from typing import Optional

from imapclient.exceptions import IMAPClientError

from ..config import DEFAULT_FOLDER
from ..crypto import decrypt_password
from ..database import session_scope
from ..models import Account
from .imap_client import (
    IMAPSyncError,
    _account_lock,
    _discover_trash_folder,
    _imap_client,
    _release_account_lock,
)

logger = logging.getLogger(__name__)


def update_imap_flags(
    account_id: int,
    folder_uid_pairs: list[tuple[str, list[int]]],
    *,
    add_flags: Optional[list[bytes]] = None,
    remove_flags: Optional[list[bytes]] = None,
    lock_timeout: float = 30.0,
) -> dict:
    """Best-effort STORE update on the IMAP server.

    ``folder_uid_pairs`` is a list of ``(folder_name, [uid, ...])`` tuples,
    grouped by folder. Reuses ``_account_lock`` so it is mutually exclusive
    with a sync running on the same account.

    Never raises - failures are logged and reported via the returned dict::

        { "ok": bool, "folders": {folder: {"added": int, "removed": int, "error": str|None}} }
    """
    result: dict = {"ok": True, "folders": {}}
    if not folder_uid_pairs:
        return result

    try:
        with session_scope() as db:
            account = db.get(Account, account_id)
            if account is None:
                result["ok"] = False
                result["error"] = "account_not_found"
                return result
            email = account.email
            encrypted = account.encrypted_password
    except Exception:
        logger.exception("Failed to load account for IMAP flag update id=%s", account_id)
        return {"ok": False, "error": "db_load_failed", "folders": {}}

    try:
        password = decrypt_password(encrypted)
    except ValueError:
        return {"ok": False, "error": "decrypt_failed", "folders": {}}

    lock = _account_lock(account_id)
    acquired = lock.acquire(blocking=True, timeout=lock_timeout)
    if not acquired:
        logger.warning("Timed out acquiring lock for flags update account=%s", account_id)
        return {"ok": False, "error": "busy", "folders": {}}

    try:
        with _imap_client(email, password) as client:
            for folder, uids in folder_uid_pairs:
                stat = {"added": 0, "removed": 0, "error": None}
                if not uids:
                    result["folders"][folder] = stat
                    continue
                try:
                    client.select_folder(folder, readonly=False)
                    if add_flags:
                        client.add_flags(uids, add_flags)
                        stat["added"] = len(uids)
                    if remove_flags:
                        client.remove_flags(uids, remove_flags)
                        stat["removed"] = len(uids)
                except IMAPClientError as exc:
                    stat["error"] = str(exc)
                    result["ok"] = False
                    logger.warning(
                        "Failed to update IMAP flags account=%s folder=%s: %s",
                        account_id, folder, exc,
                    )
                except Exception as exc:
                    stat["error"] = exc.__class__.__name__
                    result["ok"] = False
                    logger.exception(
                        "Unexpected error updating IMAP flags account=%s folder=%s",
                        account_id, folder,
                    )
                result["folders"][folder] = stat
    except IMAPSyncError as exc:
        logger.warning("IMAP unavailable for flag update account=%s: %s", account_id, exc)
        return {"ok": False, "error": str(exc), "folders": {}}
    except Exception:
        logger.exception("Unexpected update_imap_flags failure account=%s", account_id)
        return {"ok": False, "error": "unexpected", "folders": {}}
    finally:
        _release_account_lock(account_id, lock)

    return result


def delete_imap_messages(
    account_id: int,
    folder_uid_pairs: list[tuple[str, list[int]]],
    *,
    lock_timeout: float = 30.0,
) -> dict:
    """把邮件移到 Gmail 的「已删除邮件」（垃圾箱），与在 Gmail 中点「删除」一致。

    Gmail 里对 INBOX 等文件夹打 ``\\Deleted`` 再 expunge 只是去掉该标签（等于归档），
    邮件仍留在「所有邮件」中，所以要移动到带 ``\\Trash`` 标志的文件夹。
    找不到垃圾箱、或邮件本来就在垃圾箱里时，才退回 ``\\Deleted`` + expunge（彻底删除）。
    """
    result: dict = {"ok": True, "folders": {}}
    if not folder_uid_pairs:
        return result

    try:
        with session_scope() as db:
            account = db.get(Account, account_id)
            if account is None:
                return {"ok": False, "error": "account_not_found", "folders": {}}
            email = account.email
            encrypted = account.encrypted_password
    except Exception:
        logger.exception("Failed to load account for IMAP delete id=%s", account_id)
        return {"ok": False, "error": "db_load_failed", "folders": {}}

    try:
        password = decrypt_password(encrypted)
    except ValueError:
        return {"ok": False, "error": "decrypt_failed", "folders": {}}

    lock = _account_lock(account_id)
    acquired = lock.acquire(blocking=True, timeout=lock_timeout)
    if not acquired:
        logger.warning("Timed out acquiring lock for delete account=%s", account_id)
        return {"ok": False, "error": "busy", "folders": {}}

    try:
        with _imap_client(email, password) as client:
            trash = _discover_trash_folder(client)
            for folder, uids in folder_uid_pairs:
                stat = {"deleted": 0, "error": None}
                if not uids:
                    result["folders"][folder] = stat
                    continue
                try:
                    client.select_folder(folder, readonly=False)
                    if trash and folder != trash:
                        _move_uids(client, uids, trash)
                    else:
                        _expunge_uids(client, uids)
                    stat["deleted"] = len(uids)
                except IMAPClientError as exc:
                    stat["error"] = str(exc)
                    result["ok"] = False
                    logger.warning(
                        "Failed to delete IMAP messages account=%s folder=%s: %s",
                        account_id, folder, exc,
                    )
                except Exception as exc:
                    stat["error"] = exc.__class__.__name__
                    result["ok"] = False
                    logger.exception(
                        "Unexpected error deleting IMAP messages account=%s folder=%s",
                        account_id, folder,
                    )
                result["folders"][folder] = stat
    except IMAPSyncError as exc:
        logger.warning("IMAP unavailable for delete account=%s: %s", account_id, exc)
        return {"ok": False, "error": str(exc), "folders": {}}
    except Exception:
        logger.exception("Unexpected delete_imap_messages failure account=%s", account_id)
        return {"ok": False, "error": "unexpected", "folders": {}}
    finally:
        _release_account_lock(account_id, lock)

    return result


def _expunge_uids(client, uids: list[int]) -> None:
    client.delete_messages(uids)
    try:
        client.uid_expunge(uids)
    except IMAPClientError:
        # Fallback for servers without UIDPLUS support.
        client.expunge()


def _move_uids(client, uids: list[int], target: str) -> None:
    if client.has_capability("MOVE"):
        client.move(uids, target)
    else:
        client.copy(uids, target)
        _expunge_uids(client, uids)


def move_imap_messages(
    account_id: int,
    folder: str,
    uids: list[int],
    target: str = DEFAULT_FOLDER,
    *,
    lock_timeout: float = 30.0,
) -> dict:
    """把 ``folder`` 中的邮件移动到 ``target``（默认收件箱）。

    从 Gmail 垃圾邮件文件夹移到收件箱即等同于在 Gmail 中点「不是垃圾邮件」，
    Gmail 会据此调整垃圾邮件过滤。返回 ``{"ok": bool, "moved": n, "error": ...}``。
    """
    if not uids:
        return {"ok": True, "moved": 0, "error": None}
    try:
        with session_scope() as db:
            account = db.get(Account, account_id)
            if account is None:
                return {"ok": False, "moved": 0, "error": "account_not_found"}
            email = account.email
            encrypted = account.encrypted_password
        password = decrypt_password(encrypted)
    except ValueError:
        return {"ok": False, "moved": 0, "error": "decrypt_failed"}
    except Exception:
        logger.exception("Failed to load account for IMAP move id=%s", account_id)
        return {"ok": False, "moved": 0, "error": "db_load_failed"}

    lock = _account_lock(account_id)
    if not lock.acquire(blocking=True, timeout=lock_timeout):
        logger.warning("Timed out acquiring lock for move account=%s", account_id)
        return {"ok": False, "moved": 0, "error": "busy"}
    try:
        with _imap_client(email, password) as client:
            client.select_folder(folder, readonly=False)
            _move_uids(client, uids, target)
        logger.info("Moved IMAP messages account=%s %s -> %s uids=%s", account_id, folder, target, uids)
        return {"ok": True, "moved": len(uids), "error": None}
    except (IMAPSyncError, IMAPClientError) as exc:
        logger.warning("Failed to move IMAP messages account=%s folder=%s: %s", account_id, folder, exc)
        return {"ok": False, "moved": 0, "error": str(exc)}
    except Exception as exc:
        logger.exception("Unexpected move_imap_messages failure account=%s", account_id)
        return {"ok": False, "moved": 0, "error": exc.__class__.__name__}
    finally:
        _release_account_lock(account_id, lock)


def update_gmail_labels(
    account_id: int,
    folder: str,
    uids: list[int],
    *,
    add: Optional[list[str]] = None,
    remove: Optional[list[str]] = None,
    lock_timeout: float = 30.0,
) -> dict:
    """给邮件加上 / 去掉 Gmail 标签（STORE +X-GM-LABELS / -X-GM-LABELS）。"""
    if not uids or not (add or remove):
        return {"ok": True, "error": None}
    try:
        with session_scope() as db:
            account = db.get(Account, account_id)
            if account is None:
                return {"ok": False, "error": "account_not_found"}
            email = account.email
            encrypted = account.encrypted_password
        password = decrypt_password(encrypted)
    except ValueError:
        return {"ok": False, "error": "decrypt_failed"}
    except Exception:
        logger.exception("Failed to load account for label update id=%s", account_id)
        return {"ok": False, "error": "db_load_failed"}

    lock = _account_lock(account_id)
    if not lock.acquire(blocking=True, timeout=lock_timeout):
        return {"ok": False, "error": "busy"}
    try:
        with _imap_client(email, password) as client:
            client.select_folder(folder, readonly=False)
            if add:
                client.add_gmail_labels(uids, add, silent=True)
            if remove:
                client.remove_gmail_labels(uids, remove, silent=True)
        return {"ok": True, "error": None}
    except (IMAPSyncError, IMAPClientError) as exc:
        logger.warning("Failed to update Gmail labels account=%s folder=%s: %s", account_id, folder, exc)
        return {"ok": False, "error": str(exc)}
    except Exception as exc:
        logger.exception("Unexpected update_gmail_labels failure account=%s", account_id)
        return {"ok": False, "error": exc.__class__.__name__}
    finally:
        _release_account_lock(account_id, lock)

