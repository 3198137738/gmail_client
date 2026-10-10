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

相关模块
--------
* ``imap_client``：连接、账号/文件夹锁、特殊文件夹识别。
* ``imap_ops``：对已入库邮件的远程操作（标记、删除、移动）。
* ``imap_restore``：解除屏蔽后回填邮件。
"""
from __future__ import annotations

import logging
import shutil
from datetime import datetime
from email.utils import parseaddr
from time import perf_counter
from typing import Optional

from imapclient import IMAPClient
from imapclient.exceptions import IMAPClientError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import (
    DEFAULT_FOLDER,
    IMAP_FETCH_BATCH_SIZE,
    IMAP_FETCH_MAX_BYTES,
    INITIAL_SYNC_LIMIT,
    SYNC_JUNK_ENABLED,
    SYNC_SENT_ENABLED,
)
from ..crypto import decrypt_password
from ..database import session_scope
from ..models import Account, Attachment, BlockedSender, Message, SyncState, TrustedSender
from .background import submit_bg
from .imap_client import (
    IMAPSyncError,
    _FOLDER_LOCK_TIMEOUT_SEC,
    _account_lock,
    _discover_junk_folder,
    _discover_sent_folder,
    _folder_lock,
    _imap_client,
    _release_account_lock,
    _try_acquire_for_sync,
)
from .imap_ops import move_imap_messages
from .email_parser import _message_base_dir, parse_email
from .events import publish_new_mail
from .telegram_service import notify_new_mail
from ..timeutil import utcnow

logger = logging.getLogger(__name__)


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
            state.last_synced_at = utcnow()
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


def _load_trusted_sender_emails(account_id: int) -> set[str]:
    with session_scope() as db:
        rows = db.execute(
            select(TrustedSender.sender_email).where(TrustedSender.account_id == account_id)
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
        state.last_synced_at = utcnow()
        state.last_error = None


def _plan_fetch_batches(
    client: IMAPClient,
    uids: list[int],
    *,
    max_count: int = IMAP_FETCH_BATCH_SIZE,
    max_bytes: int = IMAP_FETCH_MAX_BYTES,
) -> list[list[int]]:
    """把待下载的 UID 按「封数 + 总字节数」切成多批。

    RFC822 会把整封邮件（含附件）一次读进内存，原先固定每批 50 封，遇到
    带大附件的邮件一批就可能占用数百 MB。这里先用 RFC822.SIZE 取得每封
    大小，单批总大小不超过 ``max_bytes``；单封超限的邮件独占一批。
    取大小失败时退回只按封数切分。
    """
    if not uids:
        return []
    sizes: dict[int, int] = {}
    try:
        for uid, data in client.fetch(uids, ["RFC822.SIZE"]).items():
            sizes[int(uid)] = int(data.get(b"RFC822.SIZE") or 0)
    except IMAPClientError as exc:
        logger.warning("RFC822.SIZE fetch failed, batching by count only: %s", exc)

    batches: list[list[int]] = []
    current: list[int] = []
    current_bytes = 0
    for uid in uids:
        size = sizes.get(uid, 0)
        if current and (len(current) >= max_count or current_bytes + size > max_bytes):
            batches.append(current)
            current, current_bytes = [], 0
        current.append(uid)
        current_bytes += size
    if current:
        batches.append(current)
    return batches


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

    blocked_senders = _load_blocked_sender_emails(account_id)
    trusted_senders = _load_trusted_sender_emails(account_id) if folder_kind == "junk" else set()
    rescue_uids: list[int] = []
    # 本地已有的邮件不必再下载正文
    existing_before = _existing_uids(account_id, folder, uids)
    to_fetch = [uid for uid in uids if uid not in existing_before]
    for chunk in _plan_fetch_batches(client, to_fetch):
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
        if trusted_senders:
            # 信任发件人的邮件被 Gmail 判为垃圾邮件时，不入库，改为移回收件箱，
            # 收件箱同步会再把它作为新邮件收进来。
            kept = []
            for record in records:
                if _normalize_sender_email(record["from_addr"]) in trusted_senders:
                    rescue_uids.append(int(record["uid"]))
                    shutil.rmtree(
                        _message_base_dir(account_id, folder_kind, int(record["uid"])),
                        ignore_errors=True,
                    )
                else:
                    kept.append(record)
            records = kept
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
    if rescue_uids:
        logger.info(
            "Trusted sender mail found in junk, moving to inbox account=%s uids=%s",
            account_id, rescue_uids,
        )
        submit_bg(move_imap_messages, account_id, folder, rescue_uids)
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
