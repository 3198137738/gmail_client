"""IMAP 连接与并发控制：建立连接、账号/文件夹锁、特殊文件夹识别、凭据校验。

同步（imap_service）、远程操作（imap_ops）、回填（imap_restore）共用这里的
锁，保证同一账号上的 IMAP 操作互斥。
"""
from __future__ import annotations

import logging
import socket
import ssl
import threading
from contextlib import contextmanager
from typing import Optional

from imapclient import IMAPClient
from imapclient.exceptions import IMAPClientError, LoginError

from ..config import DEFAULT_FOLDER, IMAP_HOST, IMAP_PORT, IMAP_TIMEOUT_SEC
from . import google_oauth
from .background import submit_bg

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
# 正在做整账号同步的账号。账号锁还会被标记已读、删除等短操作持有，
# 不能用「锁被占用」来判断是否在同步，否则打开一封邮件就会显示「正在同步」。
_syncing: set[int] = set()


def _try_acquire_for_sync(account_id: int, lock: threading.Lock) -> bool:
    """非阻塞获取账号锁；失败时登记待补同步。

    与 :func:`_release_account_lock` 在同一个互斥锁下进行，
    保证「获取失败并登记」和「释放并检查登记」不会互相错过。
    """
    with _locks_mutex:
        if lock.acquire(blocking=False):
            _syncing.add(account_id)
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
        _syncing.discard(account_id)
        rerun = account_id in _pending_resync
        _pending_resync.discard(account_id)
    if rerun:
        # 延迟导入：imap_service 依赖本模块，顶层导入会形成循环
        from .imap_service import safe_sync_account

        logger.info("Running deferred sync for account=%s", account_id)
        submit_bg(safe_sync_account, account_id)


def is_account_busy(account_id: int) -> bool:
    """该账号是否正在做整账号同步（页面上的「正在同步…」）。"""
    with _locks_mutex:
        return account_id in _syncing


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
            if google_oauth.is_oauth_secret(password):
                # OAuth2 授权的账号：用 refresh token 换取访问令牌，以 XOAUTH2 方式登录
                try:
                    token = google_oauth.access_token_for_secret(password)
                except google_oauth.OAuthError as exc:
                    raise IMAPSyncError(f"Google 授权已失效：{exc}") from exc
                client.oauth2_login(email, token)
            else:
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
        flags, name = entry[0], entry[2]  # entry[1] 是层级分隔符
        if any(_flag_to_str(f).lower() == wanted for f in (flags or ())):
            return name if isinstance(name, str) else name.decode("utf-8", errors="replace")
    return None


def _discover_sent_folder(client: IMAPClient) -> Optional[str]:
    """Return the IMAP folder name carrying the ``\\Sent`` special-use flag."""
    return _discover_special_folder(client, "\\Sent")


def _discover_trash_folder(client: IMAPClient) -> Optional[str]:
    """Return the IMAP folder name carrying the ``\\Trash`` special-use flag（Gmail 的「已删除邮件」）."""
    return _discover_special_folder(client, "\\Trash")


def _discover_junk_folder(client: IMAPClient) -> Optional[str]:
    """Return the IMAP folder name carrying the ``\\Junk`` special-use flag（Gmail 的垃圾邮件）."""
    return _discover_special_folder(client, "\\Junk")


def verify_credentials(email: str, password: str) -> None:
    """Try to log in to verify a new account's credentials."""
    with _imap_client(email, password) as client:
        client.select_folder(DEFAULT_FOLDER, readonly=True)
