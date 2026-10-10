"""Manual sync triggers - per account or all accounts.

Sync work is dispatched to the shared background thread pool and the HTTP
response returns immediately. Progress is observable on the accounts page
(``正在同步...`` badge while the per-account lock is held).
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from ..auth import safe_next_url
from ..database import session_scope
from ..models import Account
from ..services.background import submit_bg
from ..services.imap_service import safe_sync_account

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sync", tags=["sync"])


def _safe_next(request: Request, next_url: Optional[str]) -> str:
    """校验调用方传入的跳转地址，非法时回到账号管理页。"""
    return safe_next_url(next_url, fallback=str(request.url_for("accounts.list")))


@router.post("/{account_id}", name="sync.one")
def sync_one(
    request: Request,
    account_id: int,
    next: Optional[str] = Form(None),
):
    with session_scope() as db:
        if db.get(Account, account_id) is None:
            raise HTTPException(status_code=404, detail="Account not found")

    submit_bg(safe_sync_account, account_id)
    logger.info("Queued manual sync account=%s", account_id)
    return RedirectResponse(url=_safe_next(request, next), status_code=303)


@router.post("/", name="sync.all")
def sync_all(
    request: Request,
    next: Optional[str] = Form(None),
):
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
    return RedirectResponse(url=_safe_next(request, next), status_code=303)
