"""Manual sync triggers - per account or all accounts.

Sync work is dispatched to the shared background thread pool and the HTTP
response returns immediately. Progress is pushed to the browser via the
``sync_done`` realtime event (and shown as 「同步中」 on the accounts page).
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from ..database import session_scope
from ..models import Account
from ..services.background import submit_bg
from ..services.imap_service import safe_sync_account

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/sync", tags=["sync"])


@router.post("/{account_id}", name="sync.one")
def sync_one(account_id: int):
    with session_scope() as db:
        if db.get(Account, account_id) is None:
            raise HTTPException(status_code=404, detail="Account not found")

    submit_bg(safe_sync_account, account_id)
    logger.info("Queued manual sync account=%s", account_id)
    return {"ok": True, "queued": 1}


@router.post("", name="sync.all")
def sync_all():
    with session_scope() as db:
        account_ids = [
            int(a.id)
            for a in db.execute(
                select(Account).where(Account.enabled.is_(True))
            ).scalars().all()
        ]

    for aid in account_ids:
        submit_bg(safe_sync_account, aid)
    logger.info("Queued manual batch sync for %d accounts", len(account_ids))
    return {"ok": True, "queued": len(account_ids)}
