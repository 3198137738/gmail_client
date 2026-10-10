"""解除屏蔽发件人后，回填其在屏蔽期间被同步跳过的邮件。"""
from __future__ import annotations

import logging
from datetime import datetime

from imapclient import IMAPClient
from imapclient.exceptions import IMAPClientError
from sqlalchemy.exc import IntegrityError

from ..config import DEFAULT_FOLDER, SYNC_SENT_ENABLED
from ..crypto import decrypt_password
from ..database import session_scope
from ..models import Account
from .events import publish_new_mail
from .imap_client import IMAPSyncError, _account_lock, _discover_sent_folder, _imap_client, _release_account_lock
from .imap_service import (
    _existing_uids,
    _normalize_sender_email,
    _parse_fetched_messages,
    _persist_parsed_messages,
    _plan_fetch_batches,
)

logger = logging.getLogger(__name__)


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
    existing_before = _existing_uids(account_id, folder, uids)
    to_fetch = [uid for uid in uids if uid not in existing_before]
    for chunk in _plan_fetch_batches(client, to_fetch):
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
