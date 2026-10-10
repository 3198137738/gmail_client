"""Shared message mutation logic (mark read/unread, delete).

Extracted so both the web routes and the Telegram bot can reuse the exact same
behaviour: local DB update plus best-effort IMAP propagation (``\\Seen`` flag or
permanent delete + attachment cleanup).
"""
from __future__ import annotations

import logging
import shutil
from collections import defaultdict
from pathlib import Path
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..config import ATTACHMENTS_DIR
from ..database import session_scope
from ..models import Message
from .background import submit_bg
from .imap_service import delete_imap_messages, update_imap_flags

logger = logging.getLogger(__name__)

SEEN_FLAG = b"\\Seen"


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
    db.commit()

    for account_id, folder_uids in grouped.items():
        pairs = [(folder, uids) for folder, uids in folder_uids.items()]
        if is_read:
            submit_bg(update_imap_flags, account_id, pairs, add_flags=[SEEN_FLAG])
        else:
            submit_bg(update_imap_flags, account_id, pairs, remove_flags=[SEEN_FLAG])
    return len(changed)


# --- Standalone helpers for the Telegram bot (own session lifecycle) --------


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
    """Delete one message on the server and locally. Returns ``(ok, subject_or_error)``."""
    try:
        with session_scope() as db:
            message = db.execute(
                select(Message)
                .options(selectinload(Message.attachments))
                .where(Message.id == message_id)
            ).scalar_one_or_none()
            if message is None:
                return False, "邮件不存在或已删除"
            account_id = int(message.account_id)
            folder = message.folder
            uid = int(message.uid)
            subject = (message.subject or "(无主题)").strip()

        result = delete_imap_messages(account_id, [(folder, [uid])])
        folder_stat = result.get("folders", {}).get(folder, {})
        if folder_stat.get("error") is not None:
            return False, f"IMAP 删除失败：{folder_stat.get('error')}"

        with session_scope() as db:
            message = db.execute(
                select(Message)
                .options(selectinload(Message.attachments))
                .where(Message.id == message_id)
            ).scalar_one_or_none()
            if message is not None:
                delete_local_messages(db, [message])
        return True, subject
    except Exception as exc:
        logger.exception("delete_by_id failed id=%s", message_id)
        return False, str(exc)


def list_unread(limit: int = 10) -> list[dict]:
    """Return lightweight dicts for the most recent unread inbox messages."""
    with session_scope() as db:
        rows = db.execute(
            select(Message)
            .options(selectinload(Message.account))
            .where(Message.is_read.is_(False), Message.folder_kind == "inbox")
            .order_by(Message.date.desc().nulls_last(), Message.id.desc())
            .limit(limit)
        ).scalars().all()
        return [
            {
                "id": int(m.id),
                "subject": (m.subject or "(无主题)").strip(),
                "from_addr": (m.from_addr or "").strip(),
                "account": (m.account.email if m.account else f"#{m.account_id}"),
            }
            for m in rows
        ]


def count_unread() -> int:
    from sqlalchemy import func

    with session_scope() as db:
        return int(
            db.execute(
                select(func.count(Message.id)).where(
                    Message.is_read.is_(False), Message.folder_kind == "inbox"
                )
            ).scalar_one()
            or 0
        )
