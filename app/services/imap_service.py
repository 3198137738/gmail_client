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
* On first sync of a folder, fetch the most recent ``INITIAL_SYNC_LIMIT`` UIDs;
  older mail is then backfilled in the background by ``imap_backfill``.
* Subsequent runs request UIDs strictly greater than ``last_uid`` (incremental).
* If ``UIDVALIDITY`` changes for a folder, its local state is reset.

相关模块
--------
* ``imap_client``：连接、账号/文件夹锁、特殊文件夹识别。
* ``imap_ops``：对已入库邮件的远程操作（标记、删除、移动）。
* ``imap_restore``：解除屏蔽后回填邮件。
* ``imap_backfill``：后台回填首次同步之前的全部历史邮件。
"""
from __future__ import annotations

import hashlib
import logging
import shutil
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from email.utils import parseaddr
from time import perf_counter
from typing import Optional

from imapclient import IMAPClient
from imapclient.exceptions import IMAPClientAbortError, IMAPClientError
from sqlalchemy import delete, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import (
    DEFAULT_FOLDER,
    IMAP_FETCH_BATCH_SIZE,
    IMAP_FETCH_MAX_BYTES,
    INITIAL_SYNC_LIMIT,
    SYNC_JUNK_ENABLED,
    SYNC_SENT_ENABLED,
    SYNC_TRASH_ENABLED,
)
from ..crypto import decrypt_password
from ..database import session_scope
from ..models import Account, Attachment, BlockedSender, Message, SyncState, TrustedSender
from . import labels as gmail_labels
from . import outbox
from .background import submit_bg
from .imap_client import (
    IMAPSyncError,
    _FOLDER_LOCK_TIMEOUT_SEC,
    _account_lock,
    _discover_junk_folder,
    _discover_sent_folder,
    _discover_trash_folder,
    _folder_lock,
    _imap_client,
    _release_account_lock,
    _try_acquire_for_sync,
)
from .email_parser import _message_base_dir, parse_email
from .events import new_mail_preview, publish_mail_changed, publish_new_mail, publish_sync_done
from . import image_proxy
from .otp import extract_code
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


SEEN_FLAG = "\\Seen"
FLAGGED_FLAG = "\\Flagged"


def _has_flag(flags, name: str) -> bool:
    """IMAP 标记比较（不区分大小写；IMAPClient 返回 bytes，测试替身可能是 str）。"""
    wanted = name.lower()
    for flag in flags or ():
        text = flag.decode("ascii", "replace") if isinstance(flag, bytes) else str(flag)
        if text.lower() == wanted:
            return True
    return False


def _is_seen(flags) -> bool:
    return _has_flag(flags, SEEN_FLAG)


def _int_or_none(value) -> Optional[int]:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def header_thread_id(root: Optional[str]) -> Optional[int]:
    """会话第一封邮件的 Message-ID → 会话 id（63 位正整数，按账号区分，与 Gmail 的会话 id 不会混用）。"""
    if not root:
        return None
    return int.from_bytes(hashlib.sha256(root.encode("utf-8", "replace")).digest()[:8], "big") >> 1


def _has_gmail_ext(client) -> bool:
    """服务器是否支持 Gmail IMAP 扩展（X-GM-THRID / X-GM-LABELS 等）。"""
    try:
        return bool(client.has_capability("X-GM-EXT-1"))
    except (AttributeError, IMAPClientError):
        return False


# 补齐标签时每次 FETCH 的 UID 区间长度
LABEL_BACKFILL_RANGE = 5000


def _backfill_labels(client: IMAPClient, account_id: int, folder: str) -> int:
    """升级后第一次同步：把已入库邮件的 Gmail 标签全部取一遍（每个文件夹只做一次）。"""
    if not _has_gmail_ext(client):
        return 0
    with session_scope() as db:
        state = _get_or_create_sync_state(db, account_id, folder)
        if state.labels_backfilled:
            return 0
        last_uid = int(state.last_uid or 0)
    changed = 0
    # 按 UID 区间分段取，大文件夹不会一次把全部标签读进内存
    for start in range(1, last_uid + 1, LABEL_BACKFILL_RANGE):
        end = min(start + LABEL_BACKFILL_RANGE - 1, last_uid)
        try:
            fetched = client.fetch(f"{start}:{end}", ["X-GM-LABELS"])
        except IMAPClientError as exc:
            logger.warning("Label backfill failed account=%s folder=%s: %s", account_id, folder, exc)
            return changed
        changed += gmail_labels.apply_remote_labels(
            account_id, folder,
            {int(uid): gmail_labels.parse_labels(data.get(b"X-GM-LABELS")) for uid, data in fetched.items()},
        )
    with session_scope() as db:
        _get_or_create_sync_state(db, account_id, folder).labels_backfilled = True
    if changed:
        logger.info("Labels backfilled account=%s folder=%s messages=%s", account_id, folder, changed)
    return changed


# 每次同步最多为多少封已入库邮件补齐会话 id（升级后第一次运行时补齐历史邮件，分多次完成）
THREAD_BACKFILL_LIMIT = 2000


def _backfill_received_dates(client: IMAPClient, account_id: int, folder: str) -> int:
    """升级后第一次同步：把已入库邮件的时间改为 Gmail 收到时间（每个文件夹只做一次）。

    只取 INTERNALDATE，不下载邮件内容；按 UID 区间分段，大文件夹也不会一次读进太多数据。
    """
    with session_scope() as db:
        state = _get_or_create_sync_state(db, account_id, folder)
        if state.dates_backfilled:
            return 0
        last_uid = int(state.last_uid or 0)
    changed = 0
    for start in range(1, last_uid + 1, LABEL_BACKFILL_RANGE):
        end = min(start + LABEL_BACKFILL_RANGE - 1, last_uid)
        try:
            fetched = client.fetch(f"{start}:{end}", ["INTERNALDATE"])
        except IMAPClientError as exc:
            logger.warning("Received date backfill failed account=%s folder=%s: %s", account_id, folder, exc)
            return changed
        with session_scope() as db:
            for uid, data in fetched.items():
                when = received_date(data)
                if when is None:
                    continue
                changed += db.execute(
                    update(Message)
                    .where(
                        Message.account_id == account_id,
                        Message.folder == folder,
                        Message.uid == int(uid),
                        or_(Message.date.is_(None), Message.date != when),
                    )
                    .values(date=when)
                ).rowcount or 0
    with session_scope() as db:
        _get_or_create_sync_state(db, account_id, folder).dates_backfilled = True
    if changed:
        logger.info("Received dates backfilled account=%s folder=%s messages=%s", account_id, folder, changed)
    return changed


def _backfill_thread_ids(client: IMAPClient, account_id: int, folder: str) -> int:
    """为还没有会话 id 的本地邮件补上 X-GM-THRID，返回补齐的封数。"""
    if not _has_gmail_ext(client):
        return 0
    with session_scope() as db:
        uids = list(
            db.execute(
                select(Message.uid).where(
                    Message.account_id == account_id,
                    Message.folder == folder,
                    Message.thread_id.is_(None),
                ).order_by(Message.uid.desc()).limit(THREAD_BACKFILL_LIMIT)
            ).scalars()
        )
    if not uids:
        return 0
    try:
        fetched = client.fetch(uids, ["X-GM-THRID"])
    except IMAPClientError as exc:
        logger.warning("Thread id backfill failed account=%s folder=%s: %s", account_id, folder, exc)
        return 0
    filled = 0
    with session_scope() as db:
        for uid, data in fetched.items():
            thread_id = _int_or_none(data.get(b"X-GM-THRID"))
            if thread_id is None:
                continue
            filled += db.execute(
                update(Message)
                .where(Message.account_id == account_id, Message.folder == folder, Message.uid == int(uid))
                .values(thread_id=thread_id)
            ).rowcount or 0
    if filled:
        logger.info("Thread ids backfilled account=%s folder=%s count=%s", account_id, folder, filled)
    return filled


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
    found: set[int] = set()
    with session_scope() as db:
        for chunk in _chunks(list(uids)):
            found.update(
                int(uid) for uid in db.execute(
                    select(Message.uid).where(
                        Message.account_id == account_id,
                        Message.folder == folder,
                        Message.uid.in_(chunk),
                    )
                ).scalars()
            )
    return found


def received_date(data: dict) -> Optional[datetime]:
    """Gmail 收到邮件的时间（INTERNALDATE，IMAPClient 已转为本地时间、无时区）。

    邮件的时间以它为准，与 Gmail 网页版一致：发件方自己写的 Date 头可能不准，
    曾见过写成 2611 年的邮件，按 Date 排序会永远排在最上面。
    """
    value = data.get(b"INTERNALDATE")
    return value if isinstance(value, datetime) else None


def _parse_fetched_messages(
    *,
    account_id: int,
    folder: str,
    folder_kind: str,
    fetched_items: list[tuple[int, dict]],
    blocked_senders: set[str],
    failed_uids: Optional[set[int]] = None,
    blocked_uids: Optional[list[int]] = None,
    header_threads: bool = False,
) -> list[dict]:
    """解析下载到的邮件；传入 ``failed_uids`` 时，把没能解析的 UID 记进去。

    屏蔽发件人的邮件不入库；传入 ``blocked_uids`` 时把它们的 UID 记进去。
    ``header_threads``：服务器没有 Gmail 会话 id（Outlook 等）时，按邮件头计算会话 id。
    """
    records: list[dict] = []
    for uid, data in fetched_items:
        raw = data.get(b"RFC822")
        if not raw:
            if failed_uids is not None:
                failed_uids.add(uid)
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
            if failed_uids is not None:
                failed_uids.add(uid)
            continue

        sender_email = _normalize_sender_email(parsed.from_addr)
        if sender_email and sender_email in blocked_senders:
            logger.debug(
                "Ignored blocked sender account=%s sender=%s uid=%s",
                account_id,
                sender_email,
                uid,
            )
            if blocked_uids is not None:
                blocked_uids.append(uid)
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
                "date": received_date(data) or parsed.date,
                "body_text": parsed.body_text,
                "body_html": parsed.body_html,
                "is_read": bool(_is_seen(data.get(b"FLAGS") or ())),
                "is_starred": _has_flag(data.get(b"FLAGS"), FLAGGED_FLAG),
                "thread_id": (
                    header_thread_id(parsed.thread_root) if header_threads
                    else _int_or_none(data.get(b"X-GM-THRID"))
                ),
                "labels": gmail_labels.parse_labels(data.get(b"X-GM-LABELS")),
                "has_attachments": parsed.has_attachments,
                # 自己发出的邮件不识别验证码
                "otp_code": extract_code(parsed.subject, parsed.body_text) if folder_kind != "sent" else None,
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
                is_starred=record.get("is_starred", False),
                thread_id=record.get("thread_id"),
                has_attachments=record["has_attachments"],
                otp_code=record.get("otp_code"),
            )
            db.add(message)
            db.flush()
            if record.get("labels"):
                gmail_labels.set_message_labels(db, record["account_id"], int(message.id), record["labels"])

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
    reopen_backfill: bool = False,
) -> None:
    with session_scope() as db:
        state = _get_or_create_sync_state(db, account_id, folder)
        if reopen_backfill:
            state.backfill_done = False
        if uidvalidity and state.uidvalidity and state.uidvalidity != uidvalidity:
            # 服务器 UID 体系已变，历史回填需要从头开始
            state.backfill_before_uid = None
            state.backfill_done = False
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


# 同一封邮件下载 / 解析 / 入库连续失败达到这个次数后放弃（记 error 日志），
# 不再阻止同步进度前进；否则一封坏邮件会让后面每次同步都重试它。
MAX_UID_ATTEMPTS = 5
# (account_id, folder, uid) -> 连续失败次数（进程内，重启后重新计数）
_uid_failures: dict[tuple[int, str, int], int] = {}
# 同一封邮件连续这么多轮在下载时遇到临时错误（限流 / 断线）后视为「嫌疑」：
# 下一轮先下载其他邮件，嫌疑邮件放到最后逐封单独下载，以区分「服务器限流」和「这封邮件本身有问题」。
SUSPECT_AFTER_TRANSIENT = 2
# (account_id, folder, uid) -> 下载时连续遇到临时错误的次数
_uid_transient: dict[tuple[int, str, int], int] = {}
_uid_failures_lock = threading.Lock()


def _transient_suspects(account_id: int, folder: str, uids: list[int]) -> set[int]:
    with _uid_failures_lock:
        return {
            uid for uid in uids
            if _uid_transient.get((account_id, folder, uid), 0) >= SUSPECT_AFTER_TRANSIENT
        }


def _record_uid_outcome(
    account_id: int,
    folder: str,
    attempted: list[int],
    failed: set[int],
    transient: frozenset[int] | set[int] = frozenset(),
    transient_hit: frozenset[int] | set[int] = frozenset(),
) -> list[int]:
    """更新失败计数，返回仍需重试的失败 UID（已放弃的不含在内）。

    ``transient`` 中的 UID 因限流或连接中断没能下载，与邮件本身无关：
    下次照常重试，但不计入失败次数，否则限流持续一段时间后这些邮件会被放弃、永久丢失。
    其中 ``transient_hit`` 是真正发出了下载请求、遇到临时错误的那一批，
    累计次数用于识别「每次都让下载出错」的嫌疑邮件（见 :data:`SUSPECT_AFTER_TRANSIENT`）。
    """
    retry: list[int] = []
    with _uid_failures_lock:
        for uid in attempted:
            key = (account_id, folder, uid)
            if uid in transient:
                if uid in transient_hit:
                    _uid_transient[key] = _uid_transient.get(key, 0) + 1
                retry.append(uid)
                continue
            if uid not in failed:
                _uid_failures.pop(key, None)
                _uid_transient.pop(key, None)
                continue
            count = _uid_failures.get(key, 0) + 1
            if count >= MAX_UID_ATTEMPTS:
                _uid_failures.pop(key, None)
                _uid_transient.pop(key, None)
                logger.error(
                    "Giving up on message after %s failed attempts account=%s folder=%s uid=%s",
                    count, account_id, folder, uid,
                )
                continue
            _uid_failures[key] = count
            retry.append(uid)
    return sorted(retry)


# Gmail 限流、超出每日下载限额、服务暂时不可用时返回的响应码（RFC 5530 / Gmail 扩展），如
# "[OVERQUOTA] Account exceeded command or bandwidth limits"、"[THROTTLED]"、
# "[UNAVAILABLE] Temporary System Error"，与具体邮件无关，过一段时间会自行恢复。
# 只认带方括号的响应码，不按正文里的普通单词判断，避免把邮件本身的错误当成限流。
_TRANSIENT_ERROR_CODES = ("[THROTTLED]", "[OVERQUOTA]", "[UNAVAILABLE]", "[LIMIT]")


def _is_transient_imap_error(exc: IMAPClientError) -> bool:
    if isinstance(exc, IMAPClientAbortError):
        return True  # 连接已断开
    text = str(exc).upper()
    return any(code in text for code in _TRANSIENT_ERROR_CODES)


def _server_serving_mail(client: IMAPClient, account_id: int, folder: str, exclude: set[int]) -> bool:
    """探测服务器此刻能否正常返回邮件内容：取本地已有的一封邮件的头部（不会标为已读）。

    用于判断嫌疑邮件下载失败时，是服务器在限流，还是这封邮件本身有问题。
    """
    with session_scope() as db:
        probe_uid = db.execute(
            select(Message.uid)
            .where(
                Message.account_id == account_id,
                Message.folder == folder,
                Message.uid.not_in(list(exclude)),
            )
            .order_by(Message.uid.desc())
            .limit(1)
        ).scalar()
    if probe_uid is None:
        return False
    try:
        client.fetch([int(probe_uid)], ["BODY.PEEK[HEADER]"])
    except IMAPClientError:
        return False
    return True


class _TransientFetchError(Exception):
    """下载因限流或连接中断失败，本轮应停止，剩下的邮件下次再取。"""


def _fetch_batch(
    client: IMAPClient,
    account_id: int,
    folder: str,
    chunk: list[int],
    items: list[str],
    failed_uids: set[int],
) -> dict:
    """下载一批邮件。整批失败时逐封重试，只把真正取不到的那几封记入 ``failed_uids``。"""
    try:
        return client.fetch(chunk, items)
    except IMAPClientError as exc:
        if _is_transient_imap_error(exc):
            raise _TransientFetchError(str(exc)) from exc
        logger.warning(
            "IMAP fetch failed account=%s folder=%s uids=%s: %s",
            account_id, folder, chunk, exc,
        )
        if len(chunk) == 1:
            failed_uids.update(chunk)
            return {}
    fetched: dict = {}
    for uid in chunk:
        fetched.update(_fetch_batch(client, account_id, folder, [uid], items, failed_uids))
    return fetched


@dataclass
class IngestResult:
    saved: int = 0
    # 信任发件人被判为垃圾邮件、需要移回收件箱的 UID
    rescue_uids: list[int] = field(default_factory=list)
    newest_received: Optional[datetime] = None
    # 下载、解析或入库失败、下次需要重试的 UID（升序）
    failed_uids: list[int] = field(default_factory=list)
    # 因 Gmail 限流或连接中断提前停止（剩下的邮件下次再取）
    interrupted: bool = False

    @property
    def failed(self) -> bool:
        return bool(self.failed_uids)


def _ingest_uids(
    client: IMAPClient,
    account_id: int,
    folder: str,
    folder_kind: str,
    uids: list[int],
    *,
    apply_rules: bool = False,
) -> IngestResult:
    """下载、解析并保存指定 UID 的邮件（已入库的跳过）。增量同步与历史回填共用。

    ``apply_rules``：对新入库的邮件执行自动规则（只用于增量同步收到的新邮件）。
    """
    # 已删除邮件里的照常显示（屏蔽发件人的邮件就是被移到那里的）
    blocked_senders = _load_blocked_sender_emails(account_id) if folder_kind != "trash" else set()
    trusted_senders = _load_trusted_sender_emails(account_id) if folder_kind == "junk" else set()
    rescue_uids: list[int] = []
    # 屏蔽发件人的邮件：不在这里入库，在 Gmail 中移到已删除邮件（已发送里的不动）
    blocked_uids: Optional[list[int]] = [] if folder_kind in ("inbox", "junk") else None
    saved = 0
    persisted_uids: list[int] = []
    failed_uids: set[int] = set()
    # 本批新邮件中最新的 Gmail 接收时间（INTERNALDATE），用于计算「Gmail 收到 → 入库」延迟。
    newest_received: Optional[datetime] = None
    # 本地已有的邮件不必再下载正文；本地已删除 / 移走、远程操作还在 outbox 排队的
    # 邮件也跳过，否则会被重新下载回来
    existing_before = _existing_uids(account_id, folder, uids)
    with session_scope() as db:
        existing_before |= outbox.pending_uids(
            db, account_id, folder, ops=(outbox.OP_DELETE, outbox.OP_MOVE)
        )
    to_fetch = [uid for uid in uids if uid not in existing_before]
    fetch_items = ["RFC822", "FLAGS", "INTERNALDATE"]
    if _has_gmail_ext(client):
        fetch_items += ["X-GM-THRID", "X-GM-LABELS"]
    transient_uids: set[int] = set()
    transient_hit: set[int] = set()
    # 嫌疑邮件放到最后、逐封单独下载（见 SUSPECT_AFTER_TRANSIENT）
    suspects = _transient_suspects(account_id, folder, to_fetch)
    batches = _plan_fetch_batches(client, [uid for uid in to_fetch if uid not in suspects])
    batches += [[uid] for uid in sorted(suspects)]
    fetched_any = False      # 本轮已有邮件成功下载：说明服务器没有在限流
    convicted = False        # 本轮已认定过一封问题邮件（之后连接可能已断开，不再据此判断）
    for index, chunk in enumerate(batches):
        if len(chunk) == 1 and chunk[0] in suspects and not fetched_any and not convicted:
            # 本轮还没有邮件下载成功，无从对比：先探测服务器是否正常（必须在下载嫌疑邮件之前，
            # 它可能直接让连接断开）
            fetched_any = _server_serving_mail(client, account_id, folder, set(to_fetch))
        try:
            fetched = _fetch_batch(client, account_id, folder, chunk, fetch_items, failed_uids)
        except _TransientFetchError as exc:
            if fetched_any and not convicted and len(chunk) == 1 and chunk[0] in suspects:
                # 其他邮件都能下载，唯独这封单独下载仍然出错：问题在邮件本身，
                # 按普通失败计数（满 MAX_UID_ATTEMPTS 次放弃），不能让它永远卡住同步进度
                convicted = True
                failed_uids.add(chunk[0])
                logger.warning(
                    "Message keeps failing while others succeed, counted as failure "
                    "account=%s folder=%s uid=%s: %s",
                    account_id, folder, chunk[0], exc,
                )
                continue
            # 限流或断线时后面的批次也会失败，停在这里，剩下的下次再取（不计失败次数）
            transient_hit.update(chunk)
            transient_uids.update(uid for rest in batches[index:] for uid in rest)
            logger.warning(
                "IMAP fetch interrupted account=%s folder=%s; %s messages deferred: %s",
                account_id, folder, len(transient_uids), exc,
            )
            break
        if fetched:
            fetched_any = True

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
            failed_uids=failed_uids,
            header_threads=not _has_gmail_ext(client),
            blocked_uids=blocked_uids,
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
            persisted_uids.extend(int(r["uid"]) for r in records)
        except IntegrityError:
            logger.warning(
                "Batch insert conflict account=%s folder=%s chunk=%s; retrying one-by-one",
                account_id, folder, chunk,
            )
            for record in records:
                try:
                    saved += _persist_parsed_messages([record])
                    persisted_uids.append(int(record["uid"]))
                except IntegrityError:
                    continue
                except Exception:
                    logger.exception(
                        "Failed to persist message account=%s folder=%s uid=%s",
                        account_id, folder, record["uid"],
                    )
                    failed_uids.add(int(record["uid"]))
        except Exception:
            logger.exception(
                "Failed to persist message batch account=%s folder=%s chunk=%s",
                account_id, folder, chunk,
            )
            failed_uids.update(int(record["uid"]) for record in records)

    if blocked_uids:
        _trash_blocked(account_id, folder, blocked_uids)

    if apply_rules and persisted_uids and folder_kind in ("inbox", "junk"):
        # 延迟导入：rules 依赖 message_actions，后者又依赖本模块
        from .rules import safe_apply_to_new

        safe_apply_to_new(account_id, folder, persisted_uids)

    return IngestResult(
        saved=saved,
        rescue_uids=rescue_uids,
        newest_received=newest_received,
        failed_uids=_record_uid_outcome(
            account_id, folder, to_fetch, failed_uids | transient_uids, transient_uids, transient_hit
        ),
        interrupted=bool(transient_uids),
    )


def _trash_blocked(account_id: int, folder: str, uids: list[int]) -> None:
    """屏蔽发件人的新邮件：经 outbox 移到 Gmail 的已删除邮件（失败会一直重试），之后出现在「已删除」里。"""
    logger.info(
        "Blocked sender mail found, moving to trash account=%s folder=%s uids=%s",
        account_id, folder, uids,
    )
    with session_scope() as db:
        outbox.enqueue(db, account_id, folder, outbox.OP_DELETE, uids)
    outbox.kick([account_id])


def _move_rescued_to_inbox(account_id: int, folder: str, rescue_uids: list[int]) -> None:
    if rescue_uids:
        logger.info(
            "Trusted sender mail found in junk, moving to inbox account=%s uids=%s",
            account_id, rescue_uids,
        )
        with session_scope() as db:
            outbox.enqueue(db, account_id, folder, outbox.OP_MOVE, rescue_uids, DEFAULT_FOLDER)
        outbox.kick([account_id])


# ---- 服务器 → 本地的反向同步：已读状态、删除 / 移走 --------------------------
# 删除比对要 UID SEARCH 整个文件夹，按间隔节流：有变化（HIGHESTMODSEQ 变了）时
# 至少隔 RECONCILE_MIN_INTERVAL 才比对一次，没有变化时每 RECONCILE_INTERVAL 兜底一次。
RECONCILE_INTERVAL = timedelta(minutes=10)
RECONCILE_MIN_INTERVAL = timedelta(seconds=30)
_SQL_CHUNK = 500


def _chunks(items: list[int], size: int = _SQL_CHUNK):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def _delete_local_rows(
    account_id: int, folder: str, uids: Optional[set[int]] = None, *, wait_dirs: bool = False
) -> int:
    """删除本地某文件夹的邮件（``uids`` 为 None 时删除全部）及其附件目录。

    附件目录默认交给后台删除；``wait_dirs`` 为 True 时当场删完再返回。
    """
    with session_scope() as db:
        rows = db.execute(
            select(Message.id, Message.folder_kind, Message.uid).where(
                Message.account_id == account_id, Message.folder == folder
            )
        ).all()
        if uids is not None:
            rows = [row for row in rows if int(row.uid) in uids]
        ids = [int(row.id) for row in rows]
        for chunk in _chunks(ids):
            db.execute(delete(Attachment).where(Attachment.message_id.in_(chunk)))
            db.execute(delete(Message).where(Message.id.in_(chunk)))
    dirs = [str(_message_base_dir(account_id, row.folder_kind, int(row.uid))) for row in rows]
    if dirs:
        if wait_dirs:
            _remove_dirs(dirs)
        else:
            submit_bg(_remove_dirs, dirs)
    return len(ids)


def _remove_dirs(dirs: list[str]) -> None:
    for path in dirs:
        shutil.rmtree(path, ignore_errors=True)


def _reset_folder(account_id: int, folder: str, uidvalidity: int) -> None:
    """UIDVALIDITY 变化：旧 UID 全部失效，清空本地该文件夹并从头同步。

    不能只把 last_uid 归零：旧行会让同号的新邮件被当成「已存在」而不入库，
    outbox 里的旧 UID 操作会作用到服务器上另一封邮件，回填游标也会取错。
    """
    # 附件目录按 UID 命名（不含 UIDVALIDITY），紧接着重新下载的新邮件可能与旧邮件同号：
    # 必须当场删完，交给后台的话可能删掉刚写入的新附件。
    removed = _delete_local_rows(account_id, folder, wait_dirs=True)
    with session_scope() as db:
        dropped = outbox.drop_folder_ops(db, account_id, folder)
        state = _get_or_create_sync_state(db, account_id, folder)
        state.uidvalidity = uidvalidity
        state.last_uid = 0
        state.backfill_before_uid = None
        state.backfill_done = False
        state.highestmodseq = None
        state.reconciled_at = None
        state.labels_backfilled = False
        state.dates_backfilled = True   # 清空后重新下载的邮件都直接带收到时间
    logger.warning(
        "UIDVALIDITY changed account=%s folder=%s; cleared %s local messages, dropped %s pending ops",
        account_id, folder, removed, dropped,
    )


def _highest_modseq(client: IMAPClient, folder: str, select_info: dict) -> Optional[int]:
    value = select_info.get(b"HIGHESTMODSEQ")
    if value is None:
        try:
            if not client.has_capability("CONDSTORE"):
                return None
            value = client.folder_status(folder, [b"HIGHESTMODSEQ"]).get(b"HIGHESTMODSEQ")
        except IMAPClientError:
            return None
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _apply_remote_flags(
    client: IMAPClient, account_id: int, folder: str, last_uid: int, since: Optional[int]
) -> int:
    """把服务器上的已读状态和星标同步到本地，返回改动的邮件数。

    ``since`` 为上次的 HIGHESTMODSEQ 时只取之后变化过的邮件（CONDSTORE），
    为 None 时取全部（服务器不支持 CONDSTORE 时的兜底）。
    本地刚改过、还在 outbox 等待推送的邮件以本地为准，不被服务器旧状态覆盖。
    """
    modifiers = [f"CHANGEDSINCE {since}"] if since else None
    with_labels = _has_gmail_ext(client)
    items = ["FLAGS", "X-GM-LABELS"] if with_labels else ["FLAGS"]
    fetched = client.fetch(f"1:{last_uid}", items, modifiers=modifiers)
    flags_by_uid = {
        int(uid): data.get(b"FLAGS") for uid, data in fetched.items() if int(uid) <= last_uid
    }
    if not flags_by_uid:
        return 0
    changed = 0
    if with_labels:
        with session_scope() as db:
            pending = outbox.pending_uids(
                db, account_id, folder, ops=(outbox.OP_ADD_LABEL, outbox.OP_REMOVE_LABEL)
            )
        changed += gmail_labels.apply_remote_labels(
            account_id, folder,
            {int(uid): gmail_labels.parse_labels(data.get(b"X-GM-LABELS"))
             for uid, data in fetched.items() if int(uid) <= last_uid},
            skip_uids=pending,
        )
    with session_scope() as db:
        for column, flag in ((Message.is_read, SEEN_FLAG), (Message.is_starred, FLAGGED_FLAG)):
            skip = outbox.pending_uids(
                db, account_id, folder,
                ops=(outbox.OP_ADD_FLAGS, outbox.OP_REMOVE_FLAGS), arg=flag,
            )
            on = sorted(u for u, f in flags_by_uid.items() if _has_flag(f, flag) and u not in skip)
            off = sorted(u for u, f in flags_by_uid.items() if not _has_flag(f, flag) and u not in skip)
            for value, uids in ((True, on), (False, off)):
                for chunk in _chunks(uids):
                    changed += db.execute(
                        update(Message)
                        .where(
                            Message.account_id == account_id,
                            Message.folder == folder,
                            Message.uid.in_(chunk),
                            column.is_not(value),
                        )
                        .values({column: value})
                    ).rowcount or 0
    return changed


def _reconcile_deletions(
    client: IMAPClient, account_id: int, folder: str, last_uid: int, select_info: dict
) -> int:
    """删除本地有、服务器上已不存在的邮件（在手机 / Gmail 网页上删除或归档的），返回删除数。"""
    remote = {int(u) for u in client.search(["ALL"])}
    if not remote and int(select_info.get(b"EXISTS") or 0) > 0:
        logger.warning("Empty UID SEARCH on non-empty folder account=%s folder=%s; skip", account_id, folder)
        return 0
    with session_scope() as db:
        local = set(
            db.execute(
                select(Message.uid).where(
                    Message.account_id == account_id,
                    Message.folder == folder,
                    Message.uid <= last_uid,
                )
            ).scalars()
        )
    missing = local - remote
    if not missing:
        return 0
    return _delete_local_rows(account_id, folder, missing)


def _sync_remote_changes(
    client: IMAPClient,
    account_id: int,
    folder: str,
    select_info: dict,
    *,
    last_uid: int,
    prev_modseq: Optional[int],
    reconciled_at: Optional[datetime],
) -> tuple[int, int]:
    """同步服务器上发生的已读 / 删除变化，返回 ``(改动已读状态数, 删除数)``。出错只记日志。"""
    if last_uid <= 0:
        # 首次同步：本地还没有邮件，只记下基准（在下载之前取，之后的变化都不会漏）
        try:
            modseq = _highest_modseq(client, folder, select_info)
        except IMAPClientError:
            modseq = None
        with session_scope() as db:
            state = _get_or_create_sync_state(db, account_id, folder)
            state.highestmodseq = modseq
            state.reconciled_at = utcnow()
        return 0, 0
    modseq = _highest_modseq(client, folder, select_info)
    has_mail = int(select_info.get(b"EXISTS") or 0) > 0
    now = utcnow()
    elapsed = (now - reconciled_at) if reconciled_at else None
    reconcile = (
        elapsed is None
        or elapsed >= RECONCILE_INTERVAL
        or (modseq != prev_modseq and elapsed >= RECONCILE_MIN_INTERVAL)
    )

    changed_flags = 0
    flags_ok = True
    if has_mail and (
        (modseq is not None and prev_modseq is not None and modseq != prev_modseq)
        # 服务器不支持 CONDSTORE：随删除比对一起全量核对已读状态
        or (modseq is None and reconcile)
    ):
        try:
            since = prev_modseq if modseq is not None else None
            changed_flags = _apply_remote_flags(client, account_id, folder, last_uid, since)
        except IMAPClientError as exc:
            flags_ok = False  # 不保存新的 modseq，下次从旧基准重新拉取
            logger.warning("Remote flag sync failed account=%s folder=%s: %s", account_id, folder, exc)

    removed = 0
    if reconcile:
        try:
            removed = _reconcile_deletions(client, account_id, folder, last_uid, select_info)
        except IMAPClientError as exc:
            reconcile = False
            logger.warning("Deletion reconcile failed account=%s folder=%s: %s", account_id, folder, exc)

    with session_scope() as db:
        state = _get_or_create_sync_state(db, account_id, folder)
        if flags_ok:
            state.highestmodseq = modseq
        if reconcile:
            state.reconciled_at = now
    if changed_flags or removed:
        logger.info(
            "Remote changes applied account=%s folder=%s flags=%s removed=%s",
            account_id, folder, changed_flags, removed,
        )
    return changed_flags, removed


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
        prev_modseq = state.highestmodseq
        reconciled_at = state.reconciled_at

    try:
        select_info = client.select_folder(folder, readonly=True)
    except IMAPClientError as exc:
        raise IMAPSyncError(f"Failed to select folder '{folder}': {exc}") from exc

    uidvalidity = int(select_info.get(b"UIDVALIDITY") or 0)
    if prev_uidvalidity and uidvalidity and prev_uidvalidity != uidvalidity:
        _reset_folder(account_id, folder, uidvalidity)
        last_uid = 0
        prev_modseq = None
        reconciled_at = None

    changed_flags, removed = _sync_remote_changes(
        client, account_id, folder, select_info,
        last_uid=last_uid, prev_modseq=prev_modseq, reconciled_at=reconciled_at,
    )
    if _backfill_thread_ids(client, account_id, folder):
        changed_flags += 1   # 会话分组变了，页面需要刷新
    if _backfill_labels(client, account_id, folder):
        changed_flags += 1
    if _backfill_received_dates(client, account_id, folder):
        changed_flags += 1   # 排序变了，页面需要刷新
    if changed_flags or removed:
        publish_mail_changed(account_id, folder_kind)

    truncated = False
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
        all_uids = sorted(int(u) for u in raw_uids)
        uids = all_uids[-INITIAL_SYNC_LIMIT:]
        # 首次同步只取最新的一部分（文件夹此前为空、回填已标记完成时也一样），更早的交给回填
        truncated = len(all_uids) > len(uids)

    scanned = len(uids)
    if not uids:
        _update_sync_state(account_id, folder, uidvalidity=uidvalidity, new_last_uid=None)
        return {"saved": 0, "scanned": 0, "folder": folder, "folder_kind": folder_kind}

    # 首次同步拉的是已有的邮件，不执行自动规则（与 Gmail 过滤器只作用于新邮件一致）
    result = _ingest_uids(client, account_id, folder, folder_kind, uids, apply_rules=last_uid > 0)
    saved = result.saved
    newest_received = result.newest_received

    # 有邮件失败时，进度只推进到第一封失败邮件之前，下次同步从它开始重试；
    # 否则这些邮件既不会被增量同步（UID <= last_uid），也不会被回填（UID > 本地最小 UID），永久丢失。
    if result.failed_uids:
        safe_uid = result.failed_uids[0] - 1
        new_last_uid = safe_uid if safe_uid > last_uid else None
        logger.warning(
            "Sync incomplete account=%s folder=%s failed=%s; last_uid held at %s",
            account_id, folder, result.failed_uids, max(last_uid, safe_uid),
        )
    else:
        new_last_uid = max(uids)
    _update_sync_state(
        account_id,
        folder,
        uidvalidity=uidvalidity,
        new_last_uid=new_last_uid,
        reopen_backfill=truncated,
    )
    _move_rescued_to_inbox(account_id, folder, result.rescue_uids)
    if saved and last_uid > 0 and folder_kind == "inbox":
        # 新到的收件箱邮件：预先下载外部图片，追踪像素记录到的只是收信时间而不是打开时间。
        # 首次同步拉的是历史邮件、垃圾邮件可能借此确认地址有效，都不预取。
        try:
            image_proxy.schedule_prefetch(account_id, folder, uids)
        except Exception:
            logger.exception("Failed to schedule image prefetch account=%s", account_id)
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
    if int(results.get("trash", {}).get("saved", 0) or 0):
        # 已删除邮件不算新邮件（不提示、不通知），只让正在看「已删除」的页面刷新
        publish_mail_changed(account_id, "trash")
    if inbox_new or sent_new or junk_new:
        try:
            preview = new_mail_preview(account_id, inbox_new)
            publish_new_mail(account_id, inbox=inbox_new, sent=sent_new, junk=junk_new, preview=preview)
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


def sync_targets(client: IMAPClient, account_id: int) -> list[tuple[str, str]]:
    """本账号需要同步的 (文件夹名, 类型) 列表：收件箱，以及按配置开启的已发送 / 垃圾邮件 / 已删除邮件。"""
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
    if SYNC_TRASH_ENABLED:
        trash_folder = _discover_trash_folder(client)
        if trash_folder and trash_folder != DEFAULT_FOLDER:
            targets.append((trash_folder, "trash"))
        else:
            logger.info("No Trash folder discovered for account=%s", account_id)
    return targets


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
                if _has_gmail_ext(client):
                    try:
                        gmail_labels.sync_label_list(account_id, gmail_labels.discover_label_names(client))
                    except Exception:
                        logger.exception("Label list sync failed account=%s", account_id)
                for folder_name, kind in sync_targets(client, account_id):
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
        # 页面据此去掉「正在同步…」并刷新（不再每 5 秒整页重载）
        publish_sync_done(account_id)
