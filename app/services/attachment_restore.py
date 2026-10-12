"""本地附件文件缺失时，从 Gmail 重新下载邮件并补回附件和内嵌图片。

附件按「账号 / 文件夹类型 / UID」存放在 ATTACHMENTS_DIR 下。数据库从别的机器迁移过来
（例如先在 Windows 开发机上同步，再把数据库拷到服务器）而附件目录没有一起拷时，
数据库里有附件记录、磁盘上却没有文件，页面上的图片和附件就会打不开。

浏览器请求的附件不存在时调用 :func:`restore_message_files`：用只读方式 + BODY.PEEK 重新取回
整封邮件（不会改变已读状态），按同样的规则重新解析写出附件，并把数据库中的文件路径改为本机路径。
"""
from __future__ import annotations

import logging
import shutil
import threading
from pathlib import Path
from typing import Optional

from imapclient.exceptions import IMAPClientError
from sqlalchemy import select

from ..config import ATTACHMENTS_DIR, ATTACHMENTS_URL_PREFIX
from ..crypto import decrypt_password
from ..database import session_scope
from ..models import Account, Attachment, Message
from .email_parser import _message_base_dir, parse_email
from .imap_client import IMAPSyncError, _account_lock, _imap_client, _release_account_lock

logger = logging.getLogger(__name__)

# 同一封邮件的多张图片会同时请求：每封邮件一把锁，只下载一次
_locks: dict[int, threading.Lock] = {}
_locks_mutex = threading.Lock()
# 等待同步等操作释放账号锁的最长时间（秒）
_ACCOUNT_LOCK_TIMEOUT = 30


def _message_lock(message_id: int) -> threading.Lock:
    with _locks_mutex:
        return _locks.setdefault(message_id, threading.Lock())


def local_path(att: Attachment) -> Optional[Path]:
    """附件在本机的位置：按 public_url 推出（不依赖数据库里记录的、可能来自别的机器的 file_path）。"""
    prefix = ATTACHMENTS_URL_PREFIX.rstrip("/") + "/"
    url = att.public_url or ""
    if not url.startswith(prefix):
        return None
    candidate = (ATTACHMENTS_DIR / url[len(prefix):]).resolve()
    try:
        candidate.relative_to(ATTACHMENTS_DIR.resolve())
    except ValueError:
        return None
    return candidate


def find_message(account_id: int, folder_kind: str, uid: int) -> Optional[int]:
    with session_scope() as db:
        return db.execute(
            select(Message.id).where(
                Message.account_id == account_id,
                Message.folder_kind == folder_kind,
                Message.uid == uid,
            ).limit(1)
        ).scalar()


def _normalize_message_id(value: Optional[str]) -> str:
    return (value or "").strip().strip("<>").strip().lower()


def restore_message_files(message_id: int) -> bool:
    """重新下载邮件并写出全部附件；成功返回 True。连不上、邮件已不在 Gmail 中等返回 False。"""
    with _message_lock(message_id):
        with session_scope() as db:
            message = db.get(Message, message_id)
            if message is None:
                return False
            account = db.get(Account, message.account_id)
            if account is None:
                return False
            atts = db.execute(select(Attachment).where(Attachment.message_id == message_id)).scalars().all()
            # 等锁期间另一个请求可能已经补好了
            paths = [local_path(a) for a in atts]
            if atts and all(p is not None and p.is_file() for p in paths):
                return True
            account_id = int(message.account_id)
            folder, folder_kind, uid = message.folder, message.folder_kind, int(message.uid)
            expected_mid = _normalize_message_id(message.message_id)
            email, encrypted = account.email, account.encrypted_password

        try:
            password = decrypt_password(encrypted)
        except ValueError:
            return False

        lock = _account_lock(account_id)
        if not lock.acquire(timeout=_ACCOUNT_LOCK_TIMEOUT):
            logger.warning("Restore attachments: account busy account=%s message=%s", account_id, message_id)
            return False
        try:
            with _imap_client(email, password) as client:
                client.select_folder(folder, readonly=True)
                fetched = client.fetch([uid], ["BODY.PEEK[]"])
            raw = (fetched.get(uid) or {}).get(b"BODY[]")
        except (IMAPSyncError, IMAPClientError) as exc:
            logger.warning("Restore attachments failed account=%s message=%s: %s", account_id, message_id, exc)
            return False
        finally:
            _release_account_lock(account_id, lock)
        if not raw:
            logger.info("Restore attachments: uid %s no longer in %s (account=%s)", uid, folder, account_id)
            return False

        # 重新写出前清空该邮件的附件目录：附件重名时解析器会加 _1 后缀，目录里有残留会改变文件名
        base_dir = _message_base_dir(account_id, folder_kind, uid)
        shutil.rmtree(base_dir, ignore_errors=True)
        parsed = parse_email(raw, account_id=account_id, uid=uid, folder_kind=folder_kind)
        if expected_mid and _normalize_message_id(parsed.message_id) not in ("", expected_mid):
            # UID 已对应另一封邮件（不应发生：UIDVALIDITY 变化时本地记录会被清空），不使用
            shutil.rmtree(base_dir, ignore_errors=True)
            logger.warning("Restore attachments: Message-ID mismatch account=%s message=%s", account_id, message_id)
            return False

        new_paths = {a.public_url: a.file_path for a in parsed.attachments}
        with session_scope() as db:
            for att in db.execute(select(Attachment).where(Attachment.message_id == message_id)).scalars():
                if att.public_url in new_paths:
                    att.file_path = new_paths[att.public_url]
        logger.info(
            "Restored %s attachment files account=%s message=%s", len(parsed.attachments), account_id, message_id
        )
        return True
