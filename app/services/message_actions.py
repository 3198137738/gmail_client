"""Shared message mutation logic (mark read/unread, delete).

Extracted so both the web routes and the Telegram bot can reuse the exact same
behaviour: local DB update plus IMAP propagation through the persistent outbox
(``\\Seen`` flag or move-to-trash + attachment cleanup), retried until it succeeds.
"""
from __future__ import annotations

import logging
import shutil
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..config import ATTACHMENTS_DIR, DEFAULT_FOLDER
from ..database import session_scope
from ..models import Message, TrustedSender
from . import outbox
from .background import submit_bg
from .imap_ops import move_imap_messages
from .imap_service import _normalize_sender_email

logger = logging.getLogger(__name__)

SEEN_FLAG = b"\\Seen"
FLAGGED_FLAG = b"\\Flagged"


def _cleanup_dirs_async(base_dir_str: str, dirs: list[str]) -> None:
    """Best-effort on-disk cleanup moved out of request path."""
    try:
        base_dir = Path(base_dir_str).resolve()
    except OSError:
        return
    for raw in dirs:
        try:
            resolved = Path(raw).resolve()
        except OSError:
            continue
        try:
            resolved.relative_to(base_dir)
        except ValueError:
            continue
        if resolved.exists():
            shutil.rmtree(resolved, ignore_errors=True)


def delete_local_messages(db: Session, messages: list[Message]) -> int:
    """Remove message rows + their on-disk attachment dirs. Local only."""
    if not messages:
        return 0

    base_dir = ATTACHMENTS_DIR.resolve()
    cleanup_dirs: set[Path] = set()
    for message in messages:
        cleanup_dirs.add(base_dir / str(message.account_id) / message.folder_kind / str(message.uid))
        for attachment in message.attachments:
            try:
                resolved = Path(attachment.file_path).resolve()
            except OSError:
                continue
            try:
                rel = resolved.relative_to(base_dir)
            except ValueError:
                continue
            if len(rel.parts) >= 3:
                cleanup_dirs.add(base_dir / rel.parts[0] / rel.parts[1] / rel.parts[2])
        db.delete(message)

    db.commit()

    # Disk cleanup (especially many/large attachments) can be slow on Windows.
    # Keep requests responsive: perform filesystem deletion in background.
    cleanup_list = [str(p) for p in sorted(cleanup_dirs, key=lambda p: len(p.parts), reverse=True)]
    if cleanup_list:
        submit_bg(_cleanup_dirs_async, str(base_dir), cleanup_list)
    return len(messages)


def toggle_messages_read_state(
    db: Session,
    messages: list[Message],
    *,
    is_read: bool,
) -> int:
    """Flip local read state and schedule the matching IMAP flag update."""
    if not messages:
        return 0

    changed = [m for m in messages if bool(m.is_read) != is_read]
    if not changed:
        return 0

    grouped: dict[int, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
    for message in changed:
        message.is_read = is_read
        grouped[int(message.account_id)][message.folder].append(int(message.uid))
    op = outbox.OP_ADD_FLAGS if is_read else outbox.OP_REMOVE_FLAGS
    for account_id, folder_uids in grouped.items():
        for folder, uids in folder_uids.items():
            outbox.enqueue(db, account_id, folder, op, uids, SEEN_FLAG.decode())
    db.commit()
    outbox.kick(grouped)
    return len(changed)


def set_starred(db: Session, messages: list[Message], starred: bool) -> int:
    """加 / 取消星标：本地立即生效，``\\Flagged`` 标记经 outbox 推送到 Gmail。"""
    changed = [m for m in messages if bool(m.is_starred) != starred]
    if not changed:
        return 0
    grouped: dict[int, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
    for message in changed:
        message.is_starred = starred
        grouped[int(message.account_id)][message.folder].append(int(message.uid))
    op = outbox.OP_ADD_FLAGS if starred else outbox.OP_REMOVE_FLAGS
    for account_id, folder_uids in grouped.items():
        for folder, uids in folder_uids.items():
            outbox.enqueue(db, account_id, folder, op, uids, FLAGGED_FLAG.decode())
    db.commit()
    outbox.kick(grouped)
    return len(changed)


def move_to_trash(db: Session, messages: list[Message]) -> int:
    """删除（移入「已删除」）：本地记录立即改为「已删除」并保留，服务器上的删除写入 outbox。

    这封邮件在服务器回收站里有了新 UID、同步下来时，按 Message-ID 认领这一行
    （见 imap_service._adopt_moved_message），列表里不会出现两条，也不必等几秒才在「已删除」里看到。
    """
    targets = [m for m in messages if m.folder_kind != "trash"]
    if not targets:
        return 0
    grouped: dict[int, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
    now = datetime.now()
    for message in targets:
        grouped[int(message.account_id)][message.folder].append(int(message.uid))
        message.moved_from_kind = message.folder_kind
        message.folder_kind = "trash"
        message.moved_at = now
        message.snoozed_until = None
    for account_id, folder_uids in grouped.items():
        for folder, uids in folder_uids.items():
            outbox.enqueue(db, account_id, folder, outbox.OP_DELETE, uids)
    db.commit()
    outbox.kick(grouped)
    return len(targets)


def purge_trash(db: Session, messages: list[Message]) -> int:
    """永久删除「已删除」里的邮件。还没被服务器回收站那封认领的（刚移入）先标记 purge_pending，
    认领时再在服务器上彻底删除；其余的立即写入 outbox 并删除本地记录。"""
    targets = [m for m in messages if m.folder_kind == "trash"]
    if not targets:
        return 0
    waiting = [m for m in targets if m.moved_at is not None]
    ready = [m for m in targets if m.moved_at is None]
    for message in waiting:
        message.purge_pending = True
    grouped: dict[int, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
    for message in ready:
        grouped[int(message.account_id)][message.folder].append(int(message.uid))
    for account_id, folder_uids in grouped.items():
        for folder, uids in folder_uids.items():
            outbox.enqueue(db, account_id, folder, outbox.OP_DELETE, uids)
    if ready:
        delete_local_messages(db, ready)   # 内部会提交
    else:
        db.commit()
    outbox.kick(grouped)
    return len(targets)


def restore_from_trash(db: Session, messages: list[Message]) -> int:
    """把「已删除」里的邮件移回收件箱。

    刚从本地移入、服务器上的删除还在排队的：撤回那条删除，本地直接改回原文件夹（纯本地，立即生效）。
    服务器上已经移走、还没认领的：这几秒内无法恢复，跳过（认领后再恢复即可）。
    其余：Gmail 上的移动写入 outbox，本地记录先删掉，收件箱同步会再把它作为新邮件收进来。
    """
    targets = [m for m in messages if m.folder_kind == "trash"]
    if not targets:
        return 0
    restored = 0
    remote: list[Message] = []
    touched: set[int] = set()
    for message in targets:
        if message.moved_at is None:
            remote.append(message)
            continue
        cancelled = outbox.cancel_uids(
            db, int(message.account_id), message.folder, outbox.OP_DELETE, [int(message.uid)]
        )
        if cancelled:
            message.folder_kind = message.moved_from_kind or "inbox"
            message.moved_at = None
            message.moved_from_kind = None
            message.purge_pending = False
            restored += 1
    if remote:
        grouped: dict[int, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
        for message in remote:
            grouped[int(message.account_id)][message.folder].append(int(message.uid))
        for account_id, folder_uids in grouped.items():
            for folder, uids in folder_uids.items():
                outbox.enqueue(db, account_id, folder, outbox.OP_MOVE, uids, DEFAULT_FOLDER)
        touched.update(grouped)
        restored += delete_local_messages(db, remote)   # 内部会提交
    else:
        db.commit()
    outbox.kick(touched)
    return restored


# 归档 / 举报垃圾邮件的目标：特殊用途文件夹标记，由 imap_ops.move_imap_messages 在服务器上解析
ARCHIVE_TARGETS = {"gmail": "\All", "outlook": "\Archive"}
JUNK_TARGET = "\Junk"


def _providers(db: Session, account_ids: set[int]) -> dict[int, str]:
    from ..crypto import decrypt_password
    from ..models import Account
    from .mail_providers import GMAIL, key_for_secret

    result: dict[int, str] = {}
    for account in db.execute(select(Account).where(Account.id.in_(account_ids))).scalars():
        try:
            result[int(account.id)] = key_for_secret(decrypt_password(account.encrypted_password))
        except ValueError:
            result[int(account.id)] = GMAIL
    return result


def _move_out(db: Session, messages: list[Message], target_for) -> int:
    """把邮件移出当前文件夹：服务器上的移动写入 outbox，本地记录先删掉（同步时跳过这些 UID）。"""
    if not messages:
        return 0
    providers = _providers(db, {int(m.account_id) for m in messages})
    grouped: dict[tuple[int, str, str], list[int]] = defaultdict(list)
    for message in messages:
        target = target_for(providers.get(int(message.account_id), "gmail"))
        grouped[(int(message.account_id), message.folder, target)].append(int(message.uid))
    for (account_id, folder, target), uids in grouped.items():
        outbox.enqueue(db, account_id, folder, outbox.OP_MOVE, uids, target)
    moved = delete_local_messages(db, messages)
    outbox.kick({account_id for account_id, _f, _t in grouped})
    return moved


def archive(db: Session, messages: list[Message]) -> int:
    """归档收件箱里的邮件：Gmail 移到「所有邮件」（去掉收件箱标签），Outlook 移到「存档」。"""
    targets = [m for m in messages if m.folder_kind == "inbox"]
    return _move_out(db, targets, lambda provider: ARCHIVE_TARGETS.get(provider, ARCHIVE_TARGETS["gmail"]))


def report_spam(db: Session, messages: list[Message]) -> int:
    """举报垃圾邮件：移到服务器的垃圾邮件文件夹（服务商据此学习）。已删除 / 已发送 / 垃圾邮件中的不处理。"""
    targets = [m for m in messages if m.folder_kind not in ("junk", "trash", "sent")]
    return _move_out(db, targets, lambda _provider: JUNK_TARGET)


# --- Standalone helpers for the Telegram bot (own session lifecycle) --------


def mark_not_spam(message_id: int) -> tuple[bool, str]:
    """「这不是垃圾邮件」：信任该发件人，并把其在垃圾邮件中的所有邮件移回收件箱。

    在 Gmail 上从垃圾邮件移到收件箱，等同于在 Gmail 中点「不是垃圾邮件」。
    之后该发件人的邮件即使又被判为垃圾邮件，同步时也会自动移回收件箱。
    移动成功后才删除本地的垃圾邮件记录，收件箱同步会把它们作为新邮件收进来。
    返回 ``(ok, 提示信息)``。
    """
    try:
        with session_scope() as db:
            message = db.get(Message, message_id)
            if message is None:
                return False, "邮件不存在或已删除"
            if message.folder_kind != "junk":
                return False, "这封邮件不在垃圾邮件中"
            account_id = int(message.account_id)
            sender = _normalize_sender_email(message.from_addr)

            targets = [message]
            if sender:
                exists = db.execute(
                    select(TrustedSender.id).where(
                        TrustedSender.account_id == account_id,
                        TrustedSender.sender_email == sender,
                    )
                ).first()
                if exists is None:
                    db.add(TrustedSender(account_id=account_id, sender_email=sender))
                candidates = db.execute(
                    select(Message).where(
                        Message.account_id == account_id,
                        Message.folder_kind == "junk",
                        Message.from_addr.ilike(f"%{sender}%"),
                    )
                ).scalars().all()
                targets = [m for m in candidates if _normalize_sender_email(m.from_addr) == sender]

            groups: dict[str, dict[int, int]] = defaultdict(dict)  # folder -> {uid: message_id}
            for m in targets:
                groups[m.folder][int(m.uid)] = int(m.id)

        moved_ids: list[int] = []
        errors: list[str] = []
        for folder, uid_map in groups.items():
            result = move_imap_messages(account_id, folder, sorted(uid_map))
            if result["ok"]:
                moved_ids.extend(uid_map.values())
            else:
                errors.append(str(result["error"]))

        if moved_ids:
            with session_scope() as db:
                moved = db.execute(
                    select(Message)
                    .options(selectinload(Message.attachments))
                    .where(Message.id.in_(moved_ids))
                ).scalars().all()
                delete_local_messages(db, moved)

        logger.info(
            "not-spam account=%s sender=%s moved=%s errors=%s",
            account_id, sender, len(moved_ids), errors,
        )
        if errors:
            return False, f"移回收件箱失败：{errors[0]}"
        who = f"，已信任发件人 {sender}" if sender else ""
        return True, f"已移回收件箱 {len(moved_ids)} 封{who}"
    except Exception as exc:
        logger.exception("mark_not_spam failed id=%s", message_id)
        return False, str(exc)


def mark_read_by_id(message_id: int) -> tuple[bool, str]:
    """Mark one message read. Returns ``(ok, subject_or_error)``."""
    try:
        with session_scope() as db:
            message = db.get(Message, message_id)
            if message is None:
                return False, "邮件不存在或已删除"
            subject = (message.subject or "(无主题)").strip()
            toggle_messages_read_state(db, [message], is_read=True)
        return True, subject
    except Exception as exc:
        logger.exception("mark_read_by_id failed id=%s", message_id)
        return False, str(exc)


def delete_by_id(message_id: int) -> tuple[bool, str]:
    """删除一封邮件（Telegram 按钮）：不在「已删除」里的本地立即移入「已删除」，已在里面的永久删除。Returns ``(ok, subject_or_error)``."""
    try:
        with session_scope() as db:
            message = db.execute(
                select(Message)
                .options(selectinload(Message.attachments))
                .where(Message.id == message_id)
            ).scalar_one_or_none()
            if message is None:
                return False, "邮件不存在或已删除"
            subject = (message.subject or "(无主题)").strip()
            if message.folder_kind == "trash":
                purge_trash(db, [message])
            else:
                move_to_trash(db, [message])
        return True, subject
    except Exception as exc:
        logger.exception("delete_by_id failed id=%s", message_id)
        return False, str(exc)


# Telegram 机器人统计未读时包含的文件夹类型（收件箱 + 垃圾邮件）。
UNREAD_KINDS = ("inbox", "junk")


def list_unread(limit: int = 10) -> list[dict]:
    """Return lightweight dicts for the most recent unread inbox/junk messages."""
    with session_scope() as db:
        rows = db.execute(
            select(Message)
            .options(selectinload(Message.account))
            .where(Message.is_read.is_(False), Message.folder_kind.in_(UNREAD_KINDS))
            .order_by(Message.date.desc().nulls_last(), Message.id.desc())
            .limit(limit)
        ).scalars().all()
        return [
            {
                "id": int(m.id),
                "subject": (m.subject or "(无主题)").strip(),
                "from_addr": (m.from_addr or "").strip(),
                "account": (m.account.email if m.account else f"#{m.account_id}"),
                "folder_kind": m.folder_kind,
            }
            for m in rows
        ]


def count_unread() -> dict[str, int]:
    """按文件夹类型统计未读数，返回 ``{"inbox": n, "junk": m}``。"""
    from sqlalchemy import func

    with session_scope() as db:
        rows = db.execute(
            select(Message.folder_kind, func.count(Message.id))
            .where(Message.is_read.is_(False), Message.folder_kind.in_(UNREAD_KINDS))
            .group_by(Message.folder_kind)
        ).all()
    counts = {kind: 0 for kind in UNREAD_KINDS}
    counts.update({kind: int(n) for kind, n in rows})
    return counts
