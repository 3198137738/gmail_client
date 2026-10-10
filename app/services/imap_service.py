"""IMAP synchronization for Gmail accounts.

Sync scope
----------
For every account we sync **two folder kinds**:

* ``inbox`` → the IMAP ``INBOX`` folder.
* ``sent``  → the folder marked with the ``\\Sent`` special-use flag, which
  works for any Gmail locale (English ``[Gmail]/Sent Mail``, Chinese
  ``[Gmail]/已发送邮件``, etc.). If the server does not advertise a Sent
  folder it is silently skipped.

The DB column ``messages.folder_kind`` records the kind so the UI can filter
across multiple accounts independently of localized folder names.

Concurrency / SQLite locking
----------------------------
* All IMAP network I/O happens **outside** any DB transaction.
* DB writes use *short* transactions (one short tx per fetched batch + a tiny
  tail-update for ``sync_state``) so the SQLite writer lock is never held
  during slow network I/O.
* Per-account ``threading.Lock`` prevents the scheduler and a manual sync
  from running on the same account concurrently in one process.
* Errors are recorded in ``sync_state.last_error`` via a fresh session so a
  poisoned ORM session can never leak out.

Strategy
--------
* On first sync of a folder, fetch the most recent ``INITIAL_SYNC_LIMIT`` UIDs.
* Subsequent runs request UIDs strictly greater than ``last_uid`` (incremental).
* If ``UIDVALIDITY`` changes for a folder, its local state is reset.
"""
from __future__ import annotations

import logging
import socket
import ssl
import threading
from contextlib import contextmanager
from datetime import datetime
from email.utils import parseaddr
from time import perf_counter
from typing import Optional

from imapclient import IMAPClient
from imapclient.exceptions import IMAPClientError, LoginError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import (
    DEFAULT_FOLDER,
    IMAP_FETCH_BATCH_SIZE,
    IMAP_HOST,
    IMAP_PORT,
    IMAP_TIMEOUT_SEC,
    INITIAL_SYNC_LIMIT,
    SYNC_JUNK_ENABLED,
    SYNC_SENT_ENABLED,
)
from ..crypto import decrypt_password
from ..database import session_scope
from ..models import Account, Attachment, BlockedSender, Message, SyncState
from .background import submit_bg
from .email_parser import parse_email
from .events import publish_new_mail
from .telegram_service import notify_new_mail

logger = logging.getLogger(__name__)


class IMAPSyncError(Exception):
    """Raised when an IMAP sync attempt fails for a recoverable reason."""


_locks_mutex = threading.Lock()
_sync_locks: dict[int, threading.Lock] = {}


# 文件夹级同步锁：同一文件夹同一时刻只允许一个同步，不同文件夹（收件箱/已发送/垃圾邮件）互不阻塞。
_folder_locks: dict[tuple[int, str], threading.Lock] = {}
# 等待同一文件夹上正在进行的同步结束的最长时间。
_FOLDER_LOCK_TIMEOUT_SEC = 120


def _folder_lock(account_id: int, folder: str) -> threading.Lock:
    with _locks_mutex:
        key = (account_id, folder)
        lock = _folder_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _folder_locks[key] = lock
        return lock


def _account_lock(account_id: int) -> threading.Lock:
    with _locks_mutex:
        lock = _sync_locks.get(account_id)
        if lock is None:
            lock = threading.Lock()
            _sync_locks[account_id] = lock
        return lock


# 因账号锁被占用而跳过的同步请求；锁释放时补跑一次，避免 IDLE 通知丢失。
_pending_resync: set[int] = set()


def _try_acquire_for_sync(account_id: int, lock: threading.Lock) -> bool:
    """非阻塞获取账号锁；失败时登记待补同步。

    与 :func:`_release_account_lock` 在同一个互斥锁下进行，
    保证「获取失败并登记」和「释放并检查登记」不会互相错过。
    """
    with _locks_mutex:
        if lock.acquire(blocking=False):
            return True
        _pending_resync.add(account_id)
        return False


def _release_account_lock(account_id: int, lock: threading.Lock) -> None:
    """释放账号锁；若期间有同步请求被跳过，则立即补跑一次。"""
    with _locks_mutex:
        try:
            lock.release()
        except RuntimeError:
            return
        rerun = account_id in _pending_resync
        _pending_resync.discard(account_id)
    if rerun:
        logger.info("Running deferred sync for account=%s", account_id)
        submit_bg(safe_sync_account, account_id)


def is_account_busy(account_id: int) -> bool:
    """Return True when an IMAP operation is currently running on the account."""
    with _locks_mutex:
        lock = _sync_locks.get(account_id)
    return bool(lock and lock.locked())


@contextmanager
def _imap_client(email: str, password: str, *, timeout: Optional[int] = None):
    ssl_context = ssl.create_default_context()
    client: Optional[IMAPClient] = None
    try:
        client = IMAPClient(
            host=IMAP_HOST,
            port=IMAP_PORT,
            ssl=True,
            ssl_context=ssl_context,
            timeout=timeout or IMAP_TIMEOUT_SEC,
        )
        try:
            client.login(email, password)
        except LoginError as exc:
            raise IMAPSyncError("IMAP authentication failed") from exc
        yield client
    except (socket.timeout, socket.gaierror, OSError) as exc:
        raise IMAPSyncError(f"IMAP network error: {exc.__class__.__name__}") from exc
    finally:
        if client is not None:
            try:
                client.logout()
            except Exception:
                pass


def _flag_to_str(flag) -> str:
    if isinstance(flag, bytes):
        try:
            return flag.decode("ascii", errors="replace")
        except Exception:
            return ""
    return str(flag)


def _discover_special_folder(client: IMAPClient, flag: str) -> Optional[str]:
    """Return the IMAP folder name carrying the given special-use flag (e.g. ``\\Sent``)."""
    try:
        folders = client.list_folders()
    except IMAPClientError:
        logger.warning("IMAP LIST failed during %s folder discovery", flag, exc_info=True)
        return None
    wanted = flag.lower()
    for entry in folders:
        flags, _delim, name = entry[0], entry[1], entry[2]
        if any(_flag_to_str(f).lower() == wanted for f in (flags or ())):
            return name if isinstance(name, str) else name.decode("utf-8", errors="replace")
    return None


def _discover_sent_folder(client: IMAPClient) -> Optional[str]:
    """Return the IMAP folder name carrying the ``\\Sent`` special-use flag."""
    return _discover_special_folder(client, "\\Sent")


def _discover_junk_folder(client: IMAPClient) -> Optional[str]:
    """Return the IMAP folder name carrying the ``\\Junk`` special-use flag（Gmail 的垃圾邮件）."""
    return _discover_special_folder(client, "\\Junk")


def _get_or_create_sync_state(db: Session, account_id: int, folder: str) -> SyncState:
    state = db.execute(
        select(SyncState).where(
            SyncState.account_id == account_id,
            SyncState.folder == folder,
        )
    ).scalar_one_or_none()
    if state is None:
        state = SyncState(account_id=account_id, folder=folder, last_uid=0)
        db.add(state)
        db.flush()
    return state


def _is_seen(flags) -> bool:
    if not flags:
        return False
    for flag in flags:
        if flag in (b"\\Seen", "\\Seen"):
            return True
    return False


def _record_error(account_id: int, folder: str, error: str) -> None:
    try:
        with session_scope() as db:
            state = _get_or_create_sync_state(db, account_id, folder)
            state.last_error = error[:1000]
            state.last_synced_at = datetime.utcnow()
    except Exception:
        logger.exception("Failed to persist sync error for account=%s", account_id)


def _normalize_sender_email(value: Optional[str]) -> str:
    """Extract and normalize the sender email from a From header."""
    _name, addr = parseaddr((value or "").strip())
    return (addr or "").strip().lower()


def _load_blocked_sender_emails(account_id: int) -> set[str]:
    with session_scope() as db:
        rows = db.execute(
            select(BlockedSender.sender_email).where(BlockedSender.account_id == account_id)
        ).scalars().all()
    return {str(v).strip().lower() for v in rows if v}


def _existing_uids(account_id: int, folder: str, uids: list[int]) -> set[int]:
    if not uids:
        return set()
    with session_scope() as db:
        rows = db.execute(
            select(Message.uid).where(
                Message.account_id == account_id,
                Message.folder == folder,
                Message.uid.in_(uids),
            )
        ).scalars().all()
    return {int(uid) for uid in rows}


def _parse_fetched_messages(
    *,
    account_id: int,
    folder: str,
    folder_kind: str,
    fetched_items: list[tuple[int, dict]],
    blocked_senders: set[str],
) -> list[dict]:
    records: list[dict] = []
    for uid, data in fetched_items:
        raw = data.get(b"RFC822")
        if not raw:
            continue
        try:
            parsed = parse_email(
                raw,
                account_id=account_id,
                uid=uid,
                folder_kind=folder_kind,
            )
        except Exception:
            logger.exception("parse_email failed for account=%s uid=%s", account_id, uid)
            continue

        sender_email = _normalize_sender_email(parsed.from_addr)
        if sender_email and sender_email in blocked_senders:
            logger.debug(
                "Ignored blocked sender account=%s sender=%s uid=%s",
                account_id,
                sender_email,
                uid,
            )
            continue

        records.append(
            {
                "account_id": account_id,
                "folder": folder,
                "folder_kind": folder_kind,
                "uid": uid,
                "message_id": parsed.message_id,
                "subject": parsed.subject,
                "from_addr": parsed.from_addr,
                "to_addr": parsed.to_addr,
                "cc_addr": parsed.cc_addr,
                "bcc_addr": parsed.bcc_addr,
                "date": parsed.date,
                "body_text": parsed.body_text,
                "body_html": parsed.body_html,
                "is_read": bool(_is_seen(data.get(b"FLAGS") or ())),
                "has_attachments": parsed.has_attachments,
                "attachments": parsed.attachments,
            }
        )
    return records


def _persist_parsed_messages(records: list[dict]) -> int:
    if not records:
        return 0

    with session_scope() as db:
        for record in records:
            message = Message(
                account_id=record["account_id"],
                folder=record["folder"],
                folder_kind=record["folder_kind"],
                uid=record["uid"],
                message_id=record["message_id"],
                subject=record["subject"],
                from_addr=record["from_addr"],
                to_addr=record["to_addr"],
                cc_addr=record["cc_addr"],
                bcc_addr=record["bcc_addr"],
                date=record["date"],
                body_text=record["body_text"],
                body_html=record["body_html"],
                is_read=record["is_read"],
                has_attachments=record["has_attachments"],
            )
            db.add(message)
            db.flush()

            for att in record["attachments"]:
                db.add(
                    Attachment(
                        message_id=message.id,
                        filename=att.filename,
                        display_name=att.display_name,
                        mime_type=att.mime_type,
                        size=att.size,
                        is_inline=att.is_inline,
                        content_id=att.content_id,
                        file_path=att.file_path,
                        public_url=att.public_url,
                    )
                )
    return len(records)


def _update_sync_state(
    account_id: int,
    folder: str,
    *,
    uidvalidity: Optional[int],
    new_last_uid: Optional[int],
) -> None:
    with session_scope() as db:
        state = _get_or_create_sync_state(db, account_id, folder)
        if uidvalidity:
            state.uidvalidity = uidvalidity
        if new_last_uid is not None:
            state.last_uid = max(state.last_uid or 0, new_last_uid)
        state.last_synced_at = datetime.utcnow()
        state.last_error = None


def _sync_one_folder(
    client: IMAPClient,
    account_id: int,
    folder: str,
    folder_kind: str,
) -> dict:
    """Sync a single IMAP folder using an already-opened client."""
    started = perf_counter()
    with session_scope() as db:
        state = _get_or_create_sync_state(db, account_id, folder)
        last_uid = int(state.last_uid or 0)
        prev_uidvalidity = state.uidvalidity

    try:
        select_info = client.select_folder(folder, readonly=True)
    except IMAPClientError as exc:
        raise IMAPSyncError(f"Failed to select folder '{folder}': {exc}") from exc

    uidvalidity = int(select_info.get(b"UIDVALIDITY") or 0)
    if prev_uidvalidity and uidvalidity and prev_uidvalidity != uidvalidity:
        logger.warning(
            "UIDVALIDITY changed account=%s folder=%s; resetting",
            account_id, folder,
        )
        last_uid = 0

    if last_uid > 0:
        try:
            raw_uids = client.search(["UID", f"{last_uid + 1}:*"])
        except IMAPClientError as exc:
            raise IMAPSyncError(f"IMAP search failed: {exc}") from exc
        uids = sorted({int(u) for u in raw_uids if int(u) > last_uid})
    else:
        try:
            raw_uids = client.search(["ALL"])
        except IMAPClientError as exc:
            raise IMAPSyncError(f"IMAP search failed: {exc}") from exc
        uids = sorted(int(u) for u in raw_uids)[-INITIAL_SYNC_LIMIT:]

    scanned = len(uids)
    saved = 0
    # 本批新邮件中最新的 Gmail 接收时间（INTERNALDATE），用于计算「Gmail 收到 → 入库」延迟。
    newest_received: Optional[datetime] = None
    if not uids:
        _update_sync_state(account_id, folder, uidvalidity=uidvalidity, new_last_uid=None)
        return {"saved": 0, "scanned": 0, "folder": folder, "folder_kind": folder_kind}

    batch_size = IMAP_FETCH_BATCH_SIZE
    blocked_senders = _load_blocked_sender_emails(account_id)
    for start in range(0, len(uids), batch_size):
        chunk = uids[start:start + batch_size]
        try:
            fetched = client.fetch(chunk, ["RFC822", "FLAGS", "INTERNALDATE"])
        except IMAPClientError as exc:
            logger.warning(
                "IMAP fetch failed account=%s folder=%s uids=%s: %s",
                account_id, folder, chunk, exc,
            )
            continue

        fetched_items = sorted(
            ((int(uid), data) for uid, data in fetched.items()),
            key=lambda item: item[0],
        )
        existing = _existing_uids(account_id, folder, [uid for uid, _ in fetched_items])
        new_items = [(uid, data) for uid, data in fetched_items if uid not in existing]
        for _uid, data in new_items:
            received = data.get(b"INTERNALDATE")
            if isinstance(received, datetime) and (newest_received is None or received > newest_received):
                newest_received = received
        records = _parse_fetched_messages(
            account_id=account_id,
            folder=folder,
            folder_kind=folder_kind,
            fetched_items=new_items,
            blocked_senders=blocked_senders,
        )
        if not records:
            continue

        try:
            saved += _persist_parsed_messages(records)
        except IntegrityError:
            logger.warning(
                "Batch insert conflict account=%s folder=%s chunk=%s; retrying one-by-one",
                account_id, folder, chunk,
            )
            for record in records:
                try:
                    saved += _persist_parsed_messages([record])
                except IntegrityError:
                    continue
                except Exception:
                    logger.exception(
                        "Failed to persist message account=%s folder=%s uid=%s",
                        account_id, folder, record["uid"],
                    )
        except Exception:
            logger.exception(
                "Failed to persist message batch account=%s folder=%s chunk=%s",
                account_id, folder, chunk,
            )

    _update_sync_state(
        account_id,
        folder,
        uidvalidity=uidvalidity,
        new_last_uid=max(uids),
    )
    elapsed = perf_counter() - started
    # 仅增量同步时统计延迟；首次同步拉的是历史邮件，延迟无意义。
    # INTERNALDATE 由 IMAPClient 转换为本地时间（无时区），与 datetime.now() 直接比较；精度为秒。
    latency = ""
    if saved and last_uid > 0 and newest_received is not None:
        latency = f" latency={(datetime.now() - newest_received).total_seconds():.1f}s"
    logger.info(
        "Synced folder account=%s folder=%s kind=%s scanned=%s saved=%s elapsed=%.2fs%s",
        account_id, folder, folder_kind, scanned, saved, elapsed, latency,
    )
    return {"saved": saved, "scanned": scanned, "folder": folder, "folder_kind": folder_kind}


def _announce_new_mail(account_id: int, results: dict[str, dict]) -> None:
    """有新邮件入库时推送网页事件，并安排 Telegram 通知。"""
    inbox_new = int(results.get("inbox", {}).get("saved", 0) or 0)
    sent_new = int(results.get("sent", {}).get("saved", 0) or 0)
    junk_new = int(results.get("junk", {}).get("saved", 0) or 0)
    if inbox_new or sent_new or junk_new:
        try:
            publish_new_mail(account_id, inbox=inbox_new, sent=sent_new, junk=junk_new)
        except Exception:
            logger.debug("Failed to publish new_mail event", exc_info=True)
    # 是否真正发送（已发送/垃圾邮件的开关、账号级开关）由 notify_new_mail 决定。
    if inbox_new or sent_new or junk_new:
        try:
            submit_bg(notify_new_mail, account_id, inbox=inbox_new, sent=sent_new, junk=junk_new)
        except Exception:
            logger.debug("Failed to schedule Telegram notification", exc_info=True)


def sync_folder_with_client(
    client: IMAPClient,
    account_id: int,
    folder: str,
    folder_kind: str,
) -> Optional[dict]:
    """在调用方已登录的 IMAP 连接上增量同步单个文件夹（供 IDLE 使用）。

    省去新建连接、TLS 握手、登录和文件夹发现的开销，并且只同步发生变化的文件夹。

    只获取该文件夹的锁，不获取账号锁：同账号其他文件夹的同步、标记已读、删除等操作
    都不会阻塞它。若同一文件夹正被其他同步占用，则原地等待其结束后再在本连接上同步
    （等待超时返回 ``None``）。IMAP 出错时向上抛出，由调用方决定是否重连。
    """
    lock = _folder_lock(account_id, folder)
    if not lock.acquire(timeout=_FOLDER_LOCK_TIMEOUT_SEC):
        logger.warning("Folder busy too long, skipped account=%s folder=%s", account_id, folder)
        return None
    try:
        result = _sync_one_folder(client, account_id, folder, folder_kind)
    except IMAPSyncError as exc:
        _record_error(account_id, folder, str(exc))
        raise
    finally:
        lock.release()
    _announce_new_mail(account_id, {folder_kind: result})
    return result


def safe_sync_account(account_id: int, folder: Optional[str] = None) -> dict:
    """Sync INBOX and Sent folder for one account. Never raises.

    The ``folder`` argument is kept for backward compatibility but is ignored;
    we always sync both ``inbox`` and ``sent`` kinds in a single IMAP session.

    Returns a stats dict:
        ``{ "inbox": {...}, "sent": {...} }`` on success, or
        ``{ "skipped": true, "reason": "..." }`` / ``{ "error": "..." }``.
    """
    lock = _account_lock(account_id)
    if not _try_acquire_for_sync(account_id, lock):
        logger.info("Account busy, sync deferred until lock release account=%s", account_id)
        return {"skipped": True, "reason": "in_progress"}

    started = perf_counter()
    try:
        with session_scope() as db:
            account = db.get(Account, account_id)
            if account is None:
                return {"error": "account_not_found"}
            if not account.enabled:
                return {"skipped": True, "reason": "disabled"}
            email = account.email
            encrypted_password = account.encrypted_password

        try:
            password = decrypt_password(encrypted_password)
        except ValueError:
            msg = "Failed to decrypt stored credentials"
            _record_error(account_id, DEFAULT_FOLDER, msg)
            return {"error": msg}

        results: dict[str, dict] = {}
        try:
            with _imap_client(email, password) as client:
                targets: list[tuple[str, str]] = [(DEFAULT_FOLDER, "inbox")]
                if SYNC_SENT_ENABLED:
                    sent_folder = _discover_sent_folder(client)
                    if sent_folder and sent_folder != DEFAULT_FOLDER:
                        targets.append((sent_folder, "sent"))
                    else:
                        logger.info("No Sent folder discovered for account=%s", account_id)
                if SYNC_JUNK_ENABLED:
                    junk_folder = _discover_junk_folder(client)
                    if junk_folder and junk_folder != DEFAULT_FOLDER:
                        targets.append((junk_folder, "junk"))
                    else:
                        logger.info("No Junk folder discovered for account=%s", account_id)

                for folder_name, kind in targets:
                    folder_lock = _folder_lock(account_id, folder_name)
                    if not folder_lock.acquire(timeout=_FOLDER_LOCK_TIMEOUT_SEC):
                        logger.warning(
                            "Folder busy too long, skipped account=%s folder=%s",
                            account_id, folder_name,
                        )
                        continue
                    try:
                        results[kind] = _sync_one_folder(
                            client, account_id, folder_name, kind
                        )
                    except IMAPSyncError as exc:
                        logger.warning(
                            "Folder sync failed account=%s folder=%s: %s",
                            account_id, folder_name, exc,
                        )
                        _record_error(account_id, folder_name, str(exc))
                        results[kind] = {
                            "folder": folder_name,
                            "folder_kind": kind,
                            "error": str(exc),
                        }
                    finally:
                        folder_lock.release()
        except IMAPSyncError as exc:
            logger.warning("IMAP login/network failed account=%s: %s", account_id, exc)
            _record_error(account_id, DEFAULT_FOLDER, str(exc))
            return {"error": str(exc)}

        logger.info(
            "Account sync complete account=%s folders=%s elapsed=%.2fs",
            account_id, ",".join(results.keys()) or "none", perf_counter() - started,
        )

        _announce_new_mail(account_id, results)
        return results

    except Exception as exc:
        logger.exception("Unexpected sync error for account=%s", account_id)
        _record_error(account_id, DEFAULT_FOLDER, exc.__class__.__name__)
        return {"error": exc.__class__.__name__}
    finally:
        _release_account_lock(account_id, lock)


def _restore_sender_from_folder(
    client: IMAPClient,
    *,
    account_id: int,
    folder: str,
    folder_kind: str,
    sender_email: str,
    since_at: datetime,
) -> dict:
    """Backfill messages for one sender since a given datetime."""
    try:
        client.select_folder(folder, readonly=True)
    except IMAPClientError as exc:
        return {"folder": folder, "folder_kind": folder_kind, "saved": 0, "scanned": 0, "error": str(exc)}

    since_str = since_at.strftime("%d-%b-%Y")
    try:
        raw_uids = client.search(["FROM", sender_email, "SINCE", since_str])
    except IMAPClientError as exc:
        return {"folder": folder, "folder_kind": folder_kind, "saved": 0, "scanned": 0, "error": str(exc)}

    uids = sorted({int(uid) for uid in raw_uids if int(uid) > 0})
    scanned = len(uids)
    if not uids:
        return {"folder": folder, "folder_kind": folder_kind, "saved": 0, "scanned": 0}

    saved = 0
    for start in range(0, len(uids), IMAP_FETCH_BATCH_SIZE):
        chunk = uids[start:start + IMAP_FETCH_BATCH_SIZE]
        try:
            fetched = client.fetch(chunk, ["RFC822", "FLAGS"])
        except IMAPClientError as exc:
            logger.warning(
                "Restore fetch failed account=%s folder=%s sender=%s: %s",
                account_id, folder, sender_email, exc,
            )
            continue

        fetched_items = sorted(
            ((int(uid), data) for uid, data in fetched.items()),
            key=lambda item: item[0],
        )
        existing = _existing_uids(account_id, folder, [uid for uid, _ in fetched_items])
        new_items = [(uid, data) for uid, data in fetched_items if uid not in existing]
        records = _parse_fetched_messages(
            account_id=account_id,
            folder=folder,
            folder_kind=folder_kind,
            fetched_items=new_items,
            blocked_senders=set(),
        )
        if not records:
            continue
        try:
            saved += _persist_parsed_messages(records)
        except IntegrityError:
            for record in records:
                try:
                    saved += _persist_parsed_messages([record])
                except IntegrityError:
                    continue
                except Exception:
                    logger.exception(
                        "Restore persist failed account=%s folder=%s uid=%s",
                        account_id, folder, record["uid"],
                    )
        except Exception:
            logger.exception(
                "Restore batch persist failed account=%s folder=%s sender=%s",
                account_id, folder, sender_email,
            )

    return {"folder": folder, "folder_kind": folder_kind, "saved": saved, "scanned": scanned}


def restore_sender_messages_since(account_id: int, sender_email: str, since_at: datetime) -> dict:
    """Backfill one sender's messages since unblock cutoff time."""
    sender_norm = _normalize_sender_email(sender_email)
    if not sender_norm:
        return {"error": "invalid_sender"}

    lock = _account_lock(account_id)
    if not lock.acquire(blocking=False):
        logger.info("Restore skipped: account=%s busy", account_id)
        return {"skipped": True, "reason": "in_progress"}

    try:
        with session_scope() as db:
            account = db.get(Account, account_id)
            if account is None:
                return {"error": "account_not_found"}
            if not account.enabled:
                return {"skipped": True, "reason": "disabled"}
            email = account.email
            encrypted_password = account.encrypted_password

        try:
            password = decrypt_password(encrypted_password)
        except ValueError:
            return {"error": "decrypt_failed"}

        results: dict[str, dict] = {}
        with _imap_client(email, password) as client:
            targets: list[tuple[str, str]] = [(DEFAULT_FOLDER, "inbox")]
            if SYNC_SENT_ENABLED:
                sent_folder = _discover_sent_folder(client)
                if sent_folder and sent_folder != DEFAULT_FOLDER:
                    targets.append((sent_folder, "sent"))

            for folder_name, kind in targets:
                results[kind] = _restore_sender_from_folder(
                    client,
                    account_id=account_id,
                    folder=folder_name,
                    folder_kind=kind,
                    sender_email=sender_norm,
                    since_at=since_at,
                )

        inbox_new = int(results.get("inbox", {}).get("saved", 0) or 0)
        sent_new = int(results.get("sent", {}).get("saved", 0) or 0)
        if inbox_new or sent_new:
            try:
                publish_new_mail(account_id, inbox=inbox_new, sent=sent_new)
            except Exception:
                logger.debug("Failed to publish restored_mail event", exc_info=True)
        logger.info(
            "Restore sender complete account=%s sender=%s inbox=%s sent=%s since=%s",
            account_id, sender_norm, inbox_new, sent_new, since_at.isoformat(),
        )
        return results
    except IMAPSyncError as exc:
        return {"error": str(exc)}
    except Exception:
        logger.exception("Unexpected restore_sender_messages_since failure account=%s", account_id)
        return {"error": "unexpected"}
    finally:
        _release_account_lock(account_id, lock)


def verify_credentials(email: str, password: str) -> None:
    """Try to log in to verify a new account's credentials."""
    with _imap_client(email, password) as client:
        client.select_folder(DEFAULT_FOLDER, readonly=True)


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
    """Best-effort permanent delete for one or more folder/UID groups.

    For each selected folder we mark the target UIDs as ``\\Deleted`` and then
    expunge them from that folder. Local callers should only remove DB rows for
    groups whose ``error`` field is ``None`` in the returned payload.
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
            for folder, uids in folder_uid_pairs:
                stat = {"deleted": 0, "error": None}
                if not uids:
                    result["folders"][folder] = stat
                    continue
                try:
                    client.select_folder(folder, readonly=False)
                    client.delete_messages(uids)
                    try:
                        client.uid_expunge(uids)
                    except IMAPClientError:
                        # Fallback for servers without UIDPLUS support.
                        client.expunge()
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
