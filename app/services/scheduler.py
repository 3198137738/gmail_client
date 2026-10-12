"""Background scheduler that triggers per-account sync every N minutes.

The scheduler hands off to ``safe_sync_account(account_id)`` which owns its
DB sessions internally and uses a per-account lock to avoid colliding with a
concurrent manual sync.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

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
from . import audit, image_proxy, login_sessions
from .background import submit_bg
from .backup import safe_run_backup
from .imap_backfill import schedule_all_backfills
from .imap_service import safe_sync_account
from .outbox import process_all_due

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
    # 还没回填完历史邮件的账号继续回填（已在回填中的不会重复启动）
    schedule_all_backfills()

    logger.info("Background sync tick: queued %d accounts", len(account_ids))


def daily_maintenance() -> None:
    """清理过期的审计日志、登录会话记录和外部图片缓存。"""
    audit.safe_prune()
    try:
        login_sessions.prune()
    except Exception:
        logger.exception("Login session prune failed")
    image_proxy.safe_prune_cache()


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
    # 远程操作 outbox：每分钟执行到期的重试（首次在启动后立即执行，补上重启前没做完的）
    sched.add_job(
        process_all_due,
        "interval",
        minutes=1,
        next_run_time=datetime.now(timezone.utc) + timedelta(seconds=5),
        id="outbox",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    # 启动后稍等片刻（让首次同步 / IDLE 先跑），开始回填历史邮件
    sched.add_job(
        schedule_all_backfills,
        "date",
        run_date=datetime.now(timezone.utc) + timedelta(seconds=60),
        id="initial-backfill",
        replace_existing=True,
    )
    sched.add_job(
        daily_maintenance,
        "cron",
        hour=3,
        minute=41,
        timezone=LOCAL_TIMEZONE,
        id="daily-maintenance",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
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
