"""数据备份：数据库快照 + 密钥，打包为 backups/gmail-client-<时间>.tar.gz。

由调度器每天执行一次（见 scheduler.py），也可以手动执行：

    docker compose exec gmail-client python -m app.services.backup

恢复（先停容器）：
    tar -xzf backups/gmail-client-XXXX.tar.gz -C /tmp/restore
    cp /tmp/restore/data/gmail_client.db data/gmail_client.db
    rm -f data/gmail_client.db-wal data/gmail_client.db-shm
    cp /tmp/restore/secrets/* secrets/
    （若备份包含附件，再把 /tmp/restore/attachments 拷回 attachments/）

注意：备份里有 fernet.key，拿到备份就能解密所有账号的应用专用密码，
请妥善保管，并定期拷贝到服务器以外的地方。
"""
from __future__ import annotations

import logging
import os
import sqlite3
import stat
import tarfile
import tempfile
import threading
from datetime import datetime
from pathlib import Path

from ..config import (
    ATTACHMENTS_DIR,
    BACKUP_INCLUDE_ATTACHMENTS,
    BACKUP_KEEP,
    BACKUPS_DIR,
    DATA_DIR,
    SECRETS_DIR,
)

logger = logging.getLogger(__name__)

BACKUP_PREFIX = "gmail-client-"
BACKUP_SUFFIX = ".tar.gz"
DB_FILENAME = "gmail_client.db"

_lock = threading.Lock()


def _snapshot_database(target: Path) -> None:
    """用 SQLite 在线备份 API 生成一致的快照（WAL 模式下直接复制文件不安全）。"""
    source = sqlite3.connect(DATA_DIR / DB_FILENAME)
    try:
        dest = sqlite3.connect(target)
        try:
            source.backup(dest)
        finally:
            dest.close()
    finally:
        source.close()


def _prune(keep: int) -> list[Path]:
    backups = sorted(BACKUPS_DIR.glob(f"{BACKUP_PREFIX}*{BACKUP_SUFFIX}"))
    removed = backups[:-keep] if keep > 0 else []
    for path in removed:
        try:
            path.unlink()
        except OSError as exc:
            logger.warning("Failed to remove old backup %s: %s", path, exc)
    return removed


def run_backup(*, keep: int = BACKUP_KEEP, include_attachments: bool = BACKUP_INCLUDE_ATTACHMENTS) -> Path:
    """生成一份备份并清理旧备份，返回备份文件路径。"""
    with _lock:
        BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        final_path = BACKUPS_DIR / f"{BACKUP_PREFIX}{stamp}{BACKUP_SUFFIX}"
        partial_path = final_path.with_name(final_path.name + ".partial")

        with tempfile.TemporaryDirectory(prefix="gc-backup-") as tmp:
            snapshot = Path(tmp) / DB_FILENAME
            _snapshot_database(snapshot)
            with tarfile.open(partial_path, "w:gz") as tar:
                tar.add(snapshot, arcname=f"data/{DB_FILENAME}")
                for secret in sorted(SECRETS_DIR.glob("*")):
                    if secret.is_file():
                        tar.add(secret, arcname=f"secrets/{secret.name}")
                if include_attachments and ATTACHMENTS_DIR.is_dir():
                    tar.add(ATTACHMENTS_DIR, arcname="attachments")

        if os.name != "nt":
            os.chmod(partial_path, stat.S_IRUSR | stat.S_IWUSR)
        # 写完再改名，避免留下半个备份被当成有效备份
        partial_path.replace(final_path)
        removed = _prune(keep)
        logger.info(
            "Backup created %s (%.1f MB), removed %d old backup(s)",
            final_path.name,
            final_path.stat().st_size / 1024 / 1024,
            len(removed),
        )
        return final_path


def safe_run_backup() -> None:
    """调度器入口：失败只记日志。"""
    try:
        run_backup()
    except Exception:
        logger.exception("Scheduled backup failed")


if __name__ == "__main__":
    from ..config import setup_logging

    setup_logging()
    print(run_backup())
