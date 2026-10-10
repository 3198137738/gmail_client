"""历史邮件回填：把首次同步之前的旧邮件全部同步下来。

首次同步只取最新的 INITIAL_SYNC_LIMIT 封，让用户尽快看到新邮件；之后增量同步
只关心更新的邮件。本模块在后台从「本地最旧的那封」往前，一批
（BACKFILL_BATCH_SIZE 封）一批地回填更早的邮件，直到文件夹里的邮件全部入库。

* 进度保存在 sync_state.backfill_before_uid / backfill_done，重启后接着回填；
  出错（网络、Gmail 每日下载限额等）就停下，下一次调度时从断点继续。
* 使用独立连接和独立线程池，每批只持有该文件夹的锁、不占账号锁：
  不阻塞新邮件同步、标记已读、删除等操作，页面也不会显示「正在同步」。
* 回填的是旧邮件，不发送 Telegram 通知，也不推送「新邮件」提示。
"""
from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor

from imapclient import IMAPClient
from imapclient.exceptions import IMAPClientError
from sqlalchemy import func, select

from ..config import (
    BACKFILL_BATCH_SIZE,
    BACKFILL_MAX_CONCURRENT,
    BACKFILL_PAUSE_SEC,
    IMAP_TIMEOUT_SEC,
    SYNC_FULL_HISTORY,
)
from ..crypto import decrypt_password
from ..database import session_scope
from ..models import Account, Message, SyncState
from .imap_client import _FOLDER_LOCK_TIMEOUT_SEC, IMAPSyncError, _folder_lock, _imap_client
from .imap_service import (
    _get_or_create_sync_state,
    _ingest_uids,
    _move_rescued_to_inbox,
    safe_sync_account,
    sync_targets,
)

logger = logging.getLogger(__name__)

# 回填结果
DONE = "done"          # 该文件夹已全部回填
PROGRESS = "progress"  # 完成了一批，还有更早的邮件
SKIP = "skip"          # 首次同步还没完成或 UIDVALIDITY 变了，等下次
FAILED = "failed"      # 下载出错，停止本轮，下次从断点继续

_mutex = threading.Lock()
_running: set[int] = set()
_stop = threading.Event()
_executor: ThreadPoolExecutor | None = None


def _get_executor() -> ThreadPoolExecutor:
    global _executor
    if _executor is None:
        _executor = ThreadPoolExecutor(
            max_workers=BACKFILL_MAX_CONCURRENT,
            thread_name_prefix="gmail-backfill",
        )
    return _executor


def _mark_done(account_id: int, folder: str) -> None:
    with session_scope() as db:
        _get_or_create_sync_state(db, account_id, folder).backfill_done = True


def _backfill_batch(client: IMAPClient, account_id: int, folder: str, folder_kind: str) -> str:
    """回填一批：取 UID 小于断点的邮件中最新的 BACKFILL_BATCH_SIZE 封。"""
    with session_scope() as db:
        state = _get_or_create_sync_state(db, account_id, folder)
        if state.backfill_done:
            return DONE
        if not state.uidvalidity or not state.last_uid:
            return SKIP  # 首次同步还没跑完
        expected_validity = int(state.uidvalidity)
        cursor = state.backfill_before_uid
        if cursor is None:
            # 从本地最旧的一封开始往前回填
            oldest_local = db.execute(
                select(func.min(Message.uid)).where(
                    Message.account_id == account_id, Message.folder == folder
                )
            ).scalar()
            cursor = int(oldest_local) if oldest_local else int(state.last_uid) + 1
            state.backfill_before_uid = cursor

    try:
        select_info = client.select_folder(folder, readonly=True)
    except IMAPClientError as exc:
        raise IMAPSyncError(f"Failed to select folder '{folder}': {exc}") from exc
    if int(select_info.get(b"UIDVALIDITY") or 0) != expected_validity:
        return SKIP  # 交给增量同步先处理 UIDVALIDITY 变化

    if cursor <= 1:
        _mark_done(account_id, folder)
        return DONE
    try:
        raw_uids = client.search(["UID", f"1:{cursor - 1}"])
    except IMAPClientError as exc:
        raise IMAPSyncError(f"IMAP search failed: {exc}") from exc
    older = sorted({int(u) for u in raw_uids if 0 < int(u) < cursor})
    if not older:
        _mark_done(account_id, folder)
        logger.info("Backfill finished account=%s folder=%s", account_id, folder)
        return DONE

    batch = older[-BACKFILL_BATCH_SIZE:]
    result = _ingest_uids(client, account_id, folder, folder_kind, batch)
    _move_rescued_to_inbox(account_id, folder, result.rescue_uids)
    if result.failed:
        return FAILED

    remaining = len(older) - len(batch)
    with session_scope() as db:
        state = _get_or_create_sync_state(db, account_id, folder)
        if state.uidvalidity == expected_validity:
            state.backfill_before_uid = batch[0]
            state.backfill_done = remaining == 0
    logger.info(
        "Backfill batch account=%s folder=%s saved=%s remaining=%s",
        account_id, folder, result.saved, remaining,
    )
    return DONE if remaining == 0 else PROGRESS


def _run_backfill(account_id: int) -> None:
    try:
        with session_scope() as db:
            account = db.get(Account, account_id)
            if account is None or not account.enabled:
                return
            email = account.email
            encrypted_password = account.encrypted_password
        password = decrypt_password(encrypted_password)

        with _imap_client(email, password, timeout=max(IMAP_TIMEOUT_SEC, 60)) as client:
            pending = sync_targets(client, account_id)
            while pending and not _stop.is_set():
                for folder, kind in list(pending):
                    lock = _folder_lock(account_id, folder)
                    if not lock.acquire(timeout=_FOLDER_LOCK_TIMEOUT_SEC):
                        continue  # 该文件夹正在同步，下一轮再试
                    try:
                        status = _backfill_batch(client, account_id, folder, kind)
                    finally:
                        lock.release()
                    if status != PROGRESS:
                        pending.remove((folder, kind))
                    if _stop.is_set():
                        break
                _stop.wait(BACKFILL_PAUSE_SEC)
    except IMAPSyncError as exc:
        logger.warning("Backfill stopped account=%s: %s", account_id, exc)
    except Exception:
        logger.exception("Backfill failed account=%s", account_id)
    finally:
        with _mutex:
            _running.discard(account_id)


def sync_then_backfill(account_id: int) -> None:
    """新添加的账号：先做首次同步（最新的邮件），完成后立即开始回填历史邮件。"""
    safe_sync_account(account_id)
    schedule_backfill(account_id)


def needs_backfill(account_id: int) -> bool:
    with session_scope() as db:
        return db.execute(
            select(SyncState.id).where(
                SyncState.account_id == account_id,
                SyncState.backfill_done.is_(False),
            ).limit(1)
        ).first() is not None


def schedule_backfill(account_id: int) -> bool:
    """若该账号还有未回填的文件夹且没有正在回填，则在后台开始回填。"""
    if not SYNC_FULL_HISTORY or _stop.is_set():
        return False
    if not needs_backfill(account_id):
        return False
    with _mutex:
        if account_id in _running:
            return False
        _running.add(account_id)
    try:
        _get_executor().submit(_run_backfill, account_id)
    except RuntimeError:
        with _mutex:
            _running.discard(account_id)
        return False
    return True


def schedule_all_backfills() -> None:
    try:
        with session_scope() as db:
            ids = [int(a) for a in db.execute(
                select(Account.id).where(Account.enabled.is_(True))
            ).scalars()]
    except Exception:
        logger.exception("Failed to load accounts for backfill")
        return
    for account_id in ids:
        schedule_backfill(account_id)


def is_backfilling(account_id: int) -> bool:
    with _mutex:
        return account_id in _running


def start_backfill() -> None:
    """应用启动时调用：清除上一次关闭留下的停止标志。"""
    _stop.clear()


def shutdown_backfill() -> None:
    global _executor
    _stop.set()
    if _executor is not None:
        _executor.shutdown(wait=False, cancel_futures=True)
        _executor = None
