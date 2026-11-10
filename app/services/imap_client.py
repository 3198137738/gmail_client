"""IMAP 连接与并发控制：建立连接、账号/文件夹锁、特殊文件夹识别、凭据校验。

同步（imap_service）、远程操作（imap_ops）、回填（imap_restore）共用这里的
锁，保证同一账号上的 IMAP 操作互斥。
"""
from __future__ import annotations

import logging
import socket
import ssl
import threading
import time
from contextlib import contextmanager
from typing import Optional

from imapclient import IMAPClient
from imapclient.exceptions import IMAPClientError, LoginError

from ..config import DEFAULT_FOLDER, IMAP_COMPRESSION, IMAP_TIMEOUT_SEC
from . import mail_providers
from .background import submit_bg
from .imap_compress import enable_compression

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


# Outlook.com 回复「User is authenticated but not connected」（令牌有效、身份验证通过却连不上）有两种情况：
# 邮箱设置里没开启 IMAP（重试无用，错误信息中提示去开启），或微软个人邮箱后端的偶发故障
# （同样的令牌重试几次通常能连上，约 2–3 成的尝试成功）。
# 遇到这个回复时换新连接重试，其余登录失败（密码错误、授权被撤销等）不重试。
_NOT_CONNECTED = "authenticated but not connected"
OUTLOOK_LOGIN_ATTEMPTS = 6
_OUTLOOK_RETRY_PAUSE_SEC = 1.5


def _close_quietly(client: Optional[IMAPClient]) -> None:
    if client is None:
        return
    try:
        client.logout()
    except Exception:
        try:
            client.shutdown()
        except Exception:
            pass


def _connect_and_login(email: str, password: str, timeout: Optional[int]) -> IMAPClient:
    provider = mail_providers.for_secret(password)
    oauth = mail_providers.is_oauth_secret(password)
    token = None
    if oauth:
        # OAuth2 授权的账号：用 refresh token 换取访问令牌，以 XOAUTH2 方式登录
        try:
            token = mail_providers.access_token(email, password)
        except mail_providers.OAuthError as exc:
            raise IMAPSyncError(f"{provider.oauth_name} 授权已失效：{exc}") from exc
    attempts = OUTLOOK_LOGIN_ATTEMPTS if provider.key == mail_providers.OUTLOOK else 1
    for attempt in range(1, attempts + 1):
        client = IMAPClient(
            host=provider.imap_host,
            port=provider.imap_port,
            ssl=True,
            ssl_context=ssl.create_default_context(),
            timeout=timeout or IMAP_TIMEOUT_SEC,
        )
        try:
            if oauth:
                client.oauth2_login(email, token)
            else:
                client.login(email, password)
            if attempts > 1:
                # 这个故障也可能在登录后的第一条命令才出现：用 NAMESPACE 确认连接真的可用
                try:
                    client.namespace()
                except IMAPClientError as exc:
                    raise LoginError(str(exc)) from exc
            if attempt > 1:
                logger.info("IMAP login succeeded on attempt %s email=%s", attempt, email)
            return client
        except LoginError as exc:
            _close_quietly(client)
            # 带上服务器的原话（如 AUTHENTICATE failed / 账号未开启 IMAP），便于排查
            detail = str(exc).strip()
            if _NOT_CONNECTED in detail.lower() and attempt < attempts:
                time.sleep(_OUTLOOK_RETRY_PAUSE_SEC)
                continue
            if _NOT_CONNECTED in detail.lower():
                detail += (
                    "（请在 Outlook 网页版 设置 › 邮件 › 转发和 IMAP 中开启 IMAP；"
                    f"已开启时是微软服务器的偶发故障，已重试 {attempts} 次，稍后会自动再试）"
                )
            raise IMAPSyncError(f"IMAP authentication failed{f': {detail}' if detail else ''}") from exc
        except BaseException:
            _close_quietly(client)
            raise
    raise IMAPSyncError("IMAP authentication failed")   # 不会到这里：最后一次失败已在上面抛出


def open_connection(email: str, password: str, *, timeout: Optional[int] = None, compress: bool = False) -> IMAPClient:
    """建立并登录一条连接；``compress`` 时在支持的服务器上开启 DEFLATE 传输压缩。
    调用方负责用 ``_close_quietly`` 关闭。网络错误转为 ``IMAPSyncError``。"""
    try:
        client = _connect_and_login(email, password, timeout)
    except (socket.timeout, socket.gaierror, OSError) as exc:
        raise IMAPSyncError(f"IMAP network error: {exc.__class__.__name__}") from exc
    if compress and IMAP_COMPRESSION:
        enable_compression(client)
    return client


@contextmanager
def _imap_client(email: str, password: str, *, timeout: Optional[int] = None, compress: bool = False):
    client: Optional[IMAPClient] = None
    try:
        client = open_connection(email, password, timeout=timeout, compress=compress)
        yield client
    except (socket.timeout, socket.gaierror, OSError) as exc:
        raise IMAPSyncError(f"IMAP network error: {exc.__class__.__name__}") from exc
    finally:
        _close_quietly(client)


def _flag_to_str(flag) -> str:
    if isinstance(flag, bytes):
        try:
            return flag.decode("ascii", errors="replace")
        except Exception:
            return ""
    return str(flag)


_FALLBACK_FOLDER_NAMES = {
    "\\sent": ("sent items", "sent"),
    "\\trash": ("deleted items", "deleted"),
    "\\junk": ("junk email", "junk"),
    "\\archive": ("archive",),
}


# 特殊文件夹名按账号缓存：每次同步不必再发三次 LIST（已发送 / 垃圾邮件 / 已删除各一次）。
# 文件夹名极少变化（Gmail 改界面语言时会变），过期后自动重新发现。
FOLDER_CACHE_TTL_SEC = 3600
_folder_cache: dict[int, tuple[float, dict[str, Optional[str]]]] = {}
_folder_cache_lock = threading.Lock()


def invalidate_folder_cache(account_id: Optional[int] = None) -> None:
    with _folder_cache_lock:
        if account_id is None:
            _folder_cache.clear()
        else:
            _folder_cache.pop(account_id, None)


# 一次 LIST 的结果同时解析出这些特殊文件夹，缓存起来
_CACHED_FLAGS = ("\\sent", "\\trash", "\\junk", "\\all", "\\archive")


def _list_folders(client: IMAPClient, flag: str):
    try:
        return client.list_folders()
    except IMAPClientError:
        logger.warning("IMAP LIST failed during %s folder discovery", flag, exc_info=True)
        return None


def _match_special_folder(folders, flag: str) -> Optional[str]:
    """在 LIST 结果里找带指定特殊用途标记的文件夹；没有标记时按 Outlook / Exchange 的默认名查找。"""
    wanted = flag.lower()
    names: list[str] = []
    for entry in folders:
        flags, name = entry[0], entry[2]  # entry[1] 是层级分隔符
        name = name if isinstance(name, str) else name.decode("utf-8", errors="replace")
        if any(_flag_to_str(f).lower() == wanted for f in (flags or ())):
            return name
        names.append(name)
    for candidate in _FALLBACK_FOLDER_NAMES.get(wanted, ()):
        for name in names:
            if name.lower() == candidate:
                return name
    return None


def _cached_special_folder(client: IMAPClient, flag: str, account_id: int) -> Optional[str]:
    key = flag.lower()
    now = time.monotonic()
    with _folder_cache_lock:
        entry = _folder_cache.get(account_id)
        if entry and now - entry[0] < FOLDER_CACHE_TTL_SEC and key in entry[1]:
            return entry[1][key]
    folders = _list_folders(client, flag)
    if folders is None:
        return None   # LIST 失败不缓存，下次再试
    mapping = {f: _match_special_folder(folders, f) for f in set(_CACHED_FLAGS) | {key}}
    with _folder_cache_lock:
        _folder_cache[account_id] = (now, mapping)
    return mapping[key]


def _discover_special_folder(client: IMAPClient, flag: str, account_id: Optional[int] = None) -> Optional[str]:
    """Return the IMAP folder name carrying the given special-use flag (e.g. ``\\Sent``).
    传入 ``account_id`` 时结果按账号缓存 ``FOLDER_CACHE_TTL_SEC``（一次 LIST 解析出全部特殊文件夹）。"""
    if account_id is not None:
        return _cached_special_folder(client, flag, account_id)
    folders = _list_folders(client, flag)
    return None if folders is None else _match_special_folder(folders, flag)


def _discover_sent_folder(client: IMAPClient, account_id: Optional[int] = None) -> Optional[str]:
    """Return the IMAP folder name carrying the ``\\Sent`` special-use flag."""
    return _discover_special_folder(client, "\\Sent", account_id)


def _discover_trash_folder(client: IMAPClient, account_id: Optional[int] = None) -> Optional[str]:
    """Return the IMAP folder name carrying the ``\\Trash`` special-use flag（Gmail 的「已删除邮件」）."""
    return _discover_special_folder(client, "\\Trash", account_id)


def _discover_junk_folder(client: IMAPClient, account_id: Optional[int] = None) -> Optional[str]:
    """Return the IMAP folder name carrying the ``\\Junk`` special-use flag（Gmail 的垃圾邮件）."""
    return _discover_special_folder(client, "\\Junk", account_id)


def verify_credentials(email: str, password: str) -> None:
    """Try to log in to verify a new account's credentials."""
    with _imap_client(email, password) as client:
        client.select_folder(DEFAULT_FOLDER, readonly=True)
