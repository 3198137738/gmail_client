"""Background scheduler that triggers per-account sync every N minutes.

The scheduler hands off to ``safe_sync_account(account_id)`` which owns its
DB sessions internally and uses a per-account lock to avoid colliding with a
concurrent manual sync.
"""
from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import select

from ..config import (
    BACKUP_ENABLED,
    BACKUP_HOUR,
    DEFAULT_FOLDER,
    LOCAL_TIMEZONE,
    SYNC_INTERVAL_MINUTES,
)
from ..database import session_scope
from ..models import Account
from .background import submit_bg
from .backup import safe_run_backup
from .imap_service import safe_sync_account

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None


def sync_all_accounts() -> None:
    logger.info("Background sync tick start")
    try:
        with session_scope() as db:
            account_ids = [
                int(a.id)
                for a in db.execute(
                    select(Account).where(Account.enabled.is_(True))
                ).scalars().all()
            ]
    except Exception:
        logger.exception("Failed to load account list for scheduler tick")
        return

    for aid in account_ids:
        submit_bg(safe_sync_account, aid, DEFAULT_FOLDER)

    logger.info("Background sync tick: queued %d accounts", len(account_ids))


def start_scheduler() -> BackgroundScheduler:
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        return _scheduler
    sched = BackgroundScheduler(timezone="UTC")
    sched.add_job(
        sync_all_accounts,
        "interval",
        minutes=SYNC_INTERVAL_MINUTES,
        id="sync-all-accounts",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    if BACKUP_ENABLED:
        sched.add_job(
            safe_run_backup,
            "cron",
            hour=BACKUP_HOUR,
            minute=17,
            timezone=LOCAL_TIMEZONE,
            id="daily-backup",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=3600,
        )
    sched.start()
    _scheduler = sched
    logger.info(
        "Scheduler started; interval=%s minutes, backup=%s",
        SYNC_INTERVAL_MINUTES,
        f"daily {BACKUP_HOUR:02d}:17 {LOCAL_TIMEZONE}" if BACKUP_ENABLED else "disabled",
    )
    return sched


def shutdown_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        try:
            _scheduler.shutdown(wait=False)
        except Exception:
            pass
        _scheduler = None
