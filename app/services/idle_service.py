"""Real-time new-mail detection via IMAP IDLE.

For every enabled account we keep one long-lived IMAP connection per watched
folder (INBOX, and the Sent folder when sent-sync is on) parked in the IMAP
``IDLE`` state. Gmail pushes an untagged ``EXISTS``/``RECENT`` response as soon
as a message arrives; the worker leaves IDLE and fetches the new message
directly on the same (already authenticated and selected) connection via
``sync_folder_with_client``, which persists it and publishes a ``new_mail``
event for connected browsers.

Design notes
------------
* New mail is fetched on the IDLE connection itself (only the watched folder),
  avoiding a fresh TLS handshake + LOGIN per message. It takes only the
  per-folder lock, so the inbox/sent/junk workers of one account never block
  each other, and mark-read/delete operations (account lock) never delay it.
  If a manual/scheduled sync is already syncing the same folder, the worker
  waits for it to finish and then syncs on its own connection.
* ``idle_check`` blocks only ``IDLE_POLL_SEC`` at a time so a stop request is
  honoured promptly. Every ``IDLE_REFRESH_SEC`` the worker leaves IDLE, runs a
  cheap catch-up check (UID SEARCH) and re-enters IDLE; together with TCP
  keepalive this detects dead connections quickly and covers missed pushes.
* All failures are caught and retried with a fixed backoff; a permanently bad
  account simply keeps retrying without taking the process down. The periodic
  scheduler remains as a safety net for anything IDLE misses.
"""
from __future__ import annotations

import logging
import socket
import threading
from time import monotonic
from typing import Optional

from imapclient.exceptions import IMAPClientError

from ..config import (
    DEFAULT_FOLDER,
    IDLE_POLL_SEC,
    IDLE_RECONNECT_BACKOFF_SEC,
    IDLE_REFRESH_SEC,
    REALTIME_ENABLED,
    SYNC_JUNK_ENABLED,
    SYNC_SENT_ENABLED,
)
from ..crypto import decrypt_password
from ..database import session_scope
from ..models import Account
from sqlalchemy import select

from .background import submit_bg
from .imap_client import _discover_junk_folder, _discover_sent_folder, _imap_client
from .imap_service import safe_sync_account, sync_folder_with_client

logger = logging.getLogger(__name__)

# Give IDLE sockets enough headroom to outlast a poll wait without tripping the
# read timeout.
_IDLE_SOCKET_TIMEOUT = IDLE_POLL_SEC + 30
# 账号没有对应文件夹（如无 Sent/Junk）时，隔多久重新登录检查一次。
_NO_FOLDER_RECHECK_SEC = 600


def _has_new_mail(responses) -> bool:
    """True when an IDLE response batch signals newly arrived messages."""
    for item in responses or ():
        if not item:
            continue
        token = item[1] if len(item) > 1 else None
        if isinstance(token, bytes):
            token = token.upper()
            if token in (b"EXISTS", b"RECENT"):
                return True
    return False


def _enable_tcp_keepalive(client) -> None:
    """开启 TCP keepalive，尽快发现被 NAT/防火墙静默断开的 IDLE 连接。"""
    try:
        sock = client.socket()
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        # 空闲 60 秒后开始探测，每 15 秒一次，连续 4 次无响应即判定断开（约 2 分钟）。
        for name, value in (("TCP_KEEPIDLE", 60), ("TCP_KEEPINTVL", 15), ("TCP_KEEPCNT", 4)):
            if hasattr(socket, name):
                sock.setsockopt(socket.IPPROTO_TCP, getattr(socket, name), value)
    except Exception:
        logger.debug("Failed to enable TCP keepalive on IDLE socket", exc_info=True)


class IdleWorker(threading.Thread):
    """Watches a single (account, folder kind) over a parked IMAP connection."""

    def __init__(
        self,
        *,
        account_id: int,
        folder_kind: str,
        email: str,
        password: str,
    ) -> None:
        super().__init__(
            name=f"idle-{account_id}-{folder_kind}",
            daemon=True,
        )
        self.account_id = account_id
        self.folder_kind = folder_kind
        self._email = email
        self._password = password
        self._stop = threading.Event()

    def signature(self) -> tuple[str, str]:
        """Identity for reconciliation: restart when email/password changes."""
        return (self._email, self._password)

    def stop(self) -> None:
        self._stop.set()

    def _resolve_folder(self, client) -> Optional[str]:
        if self.folder_kind == "inbox":
            return DEFAULT_FOLDER
        if self.folder_kind == "junk":
            folder = _discover_junk_folder(client)
        else:
            folder = _discover_sent_folder(client)
        if not folder or folder == DEFAULT_FOLDER:
            return None
        return folder

    def run(self) -> None:
        logger.info("IDLE worker started account=%s kind=%s", self.account_id, self.folder_kind)
        while not self._stop.is_set():
            try:
                self._run_session()
            except Exception as exc:
                logger.warning(
                    "IDLE session error account=%s kind=%s: %s",
                    self.account_id, self.folder_kind, exc,
                )
            if self._stop.wait(IDLE_RECONNECT_BACKOFF_SEC):
                break
        logger.info("IDLE worker stopped account=%s kind=%s", self.account_id, self.folder_kind)

    def _run_session(self) -> None:
        with _imap_client(self._email, self._password, timeout=_IDLE_SOCKET_TIMEOUT) as client:
            folder = self._resolve_folder(client)
            if not folder:
                # No Sent folder advertised - nothing to watch in real time.
                logger.info(
                    "No folder to IDLE-watch account=%s kind=%s; idling out",
                    self.account_id, self.folder_kind,
                )
                self._stop.wait(_NO_FOLDER_RECHECK_SEC)
                return

            _enable_tcp_keepalive(client)
            client.select_folder(folder, readonly=True)
            # Catch up on anything that arrived while we were disconnected.
            self._sync_on_connection(client, folder, trigger="connect")

            client.idle()
            in_idle = True
            idle_since = monotonic()
            try:
                while not self._stop.is_set():
                    responses = client.idle_check(timeout=IDLE_POLL_SEC)
                    if self._stop.is_set():
                        break
                    # 收到新邮件通知时立即同步；否则每 IDLE_REFRESH_SEC 秒退出 IDLE 做一次
                    # 兜底检查（只做 UID SEARCH，无新邮件时开销极小），既能补上 Gmail
                    # 偶尔漏发的通知，也能尽快发现已失效的连接。
                    pushed = _has_new_mail(responses)
                    if pushed or monotonic() - idle_since >= IDLE_REFRESH_SEC:
                        client.idle_done()
                        in_idle = False
                        self._sync_on_connection(
                            client, folder, trigger="push" if pushed else "periodic"
                        )
                        client.idle()
                        in_idle = True
                        idle_since = monotonic()
            finally:
                if in_idle:
                    try:
                        client.idle_done()
                    except (IMAPClientError, OSError):
                        pass

    def _sync_on_connection(self, client, folder: str, *, trigger: str) -> None:
        """直接在当前 IDLE 连接上拉取新邮件，省去新建连接和登录的时间。

        ``trigger`` 记录本次收信的触发来源，便于分析延迟：
        push = Gmail IDLE 推送，periodic = 定时兜底检查，connect = 连接建立后的补收。
        """
        try:
            result = sync_folder_with_client(client, self.account_id, folder, self.folder_kind)
            if result and result.get("saved"):
                logger.info(
                    "IDLE fetched account=%s kind=%s trigger=%s saved=%s",
                    self.account_id, self.folder_kind, trigger, result["saved"],
                )
        except Exception:
            # 连接可能已损坏：先用新连接做一次完整同步，避免漏信，再由外层重连。
            submit_bg(safe_sync_account, self.account_id)
            raise


class IdleManager:
    """Owns the set of running :class:`IdleWorker` threads.

    :meth:`reconcile` diffs the currently running workers against the set of
    enabled accounts and starts/stops/restarts threads accordingly. It is safe
    to call repeatedly (startup, and whenever accounts change).
    """

    def __init__(self) -> None:
        self._workers: dict[tuple[int, str], IdleWorker] = {}
        self._lock = threading.Lock()

    def _desired_kinds(self) -> tuple[str, ...]:
        kinds = ["inbox"]
        if SYNC_SENT_ENABLED:
            kinds.append("sent")
        if SYNC_JUNK_ENABLED:
            kinds.append("junk")
        return tuple(kinds)

    def reconcile(self) -> None:
        if not REALTIME_ENABLED:
            return

        desired: dict[tuple[int, str], tuple[str, str]] = {}
        try:
            with session_scope() as db:
                accounts = db.execute(
                    select(Account).where(Account.enabled.is_(True))
                ).scalars().all()
                for account in accounts:
                    try:
                        password = decrypt_password(account.encrypted_password)
                    except ValueError:
                        logger.warning(
                            "Skipping IDLE for account=%s: cannot decrypt credentials",
                            account.id,
                        )
                        continue
                    for kind in self._desired_kinds():
                        desired[(int(account.id), kind)] = (account.email, password)
        except Exception:
            logger.exception("IDLE reconcile failed to load accounts")
            return

        with self._lock:
            # Stop workers no longer wanted or whose credentials changed.
            for key, worker in list(self._workers.items()):
                if key not in desired or worker.signature() != desired[key]:
                    worker.stop()
                    del self._workers[key]

            # Start workers that should exist but don't yet.
            for key, (email, password) in desired.items():
                if key not in self._workers:
                    worker = IdleWorker(
                        account_id=key[0],
                        folder_kind=key[1],
                        email=email,
                        password=password,
                    )
                    worker.start()
                    self._workers[key] = worker

        logger.info(
            "IDLE reconcile complete: %d worker(s) active", len(self._workers)
        )

    def stop_all(self) -> None:
        with self._lock:
            workers = list(self._workers.values())
            self._workers.clear()
        for worker in workers:
            worker.stop()


idle_manager = IdleManager()


def start_idle() -> None:
    if not REALTIME_ENABLED:
        logger.info("Real-time IDLE is disabled (REALTIME_ENABLED=false)")
        return
    idle_manager.reconcile()


def reconcile_idle() -> None:
    idle_manager.reconcile()


def shutdown_idle() -> None:
    idle_manager.stop_all()
