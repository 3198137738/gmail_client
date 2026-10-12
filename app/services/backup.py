"""数据备份：数据库快照 + 密钥，打包为 backups/gmail-client-<时间>.tar.gz。

设置了 BACKUP_PUBLIC_KEY 时先加密再落盘（gmail-client-<时间>.tar.gz.enc），服务器上
只有公钥，解密要用你离线保管的私钥；再按配置自动上传到 Telegram / WebDAV（异地）。

由调度器每天执行一次（见 scheduler.py），也可以手动执行：

    docker compose exec gmail-client python -m app.services.backup

恢复（先停容器）：
    # 加密备份先解密（在保存私钥的电脑上执行，私钥文件只含 keygen 输出的那一行私钥）
    python -m app.services.backup_crypto decrypt private.key gmail-client-XXXX.tar.gz.enc backup.tar.gz
    tar -xzf backup.tar.gz -C /tmp/restore
    cp /tmp/restore/data/gmail_client.db data/gmail_client.db
    rm -f data/gmail_client.db-wal data/gmail_client.db-shm
    cp /tmp/restore/secrets/* secrets/
    （若备份包含附件，再把 /tmp/restore/attachments 拷回 attachments/）

注意：备份里有 fernet.key，拿到未加密的备份就能解密所有账号的应用专用密码。
"""
from __future__ import annotations

import base64
import json
import logging
import os
import sqlite3
import stat
import tarfile
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Optional

from ..config import (
    ATTACHMENTS_DIR,
    BACKUP_INCLUDE_ATTACHMENTS,
    BACKUP_KEEP,
    BACKUP_PUBLIC_KEY,
    BACKUP_UPLOAD_TELEGRAM,
    BACKUP_UPLOAD_TIMEOUT_SEC,
    BACKUP_WEBDAV_PASSWORD,
    BACKUP_WEBDAV_URL,
    BACKUP_WEBDAV_USER,
    BACKUPS_DIR,
    DATA_DIR,
    SECRETS_DIR,
)
from . import backup_crypto

logger = logging.getLogger(__name__)

BACKUP_PREFIX = "gmail-client-"
BACKUP_SUFFIX = ".tar.gz"
ENCRYPTED_SUFFIX = ".tar.gz.enc"
DB_FILENAME = "gmail_client.db"
STATUS_KEY = "backup_last_status"
# Telegram Bot API 上传文件上限 50 MB，留一点余量
TELEGRAM_MAX_BYTES = 49 * 1024 * 1024

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


def _list_backups() -> list[Path]:
    found = list(BACKUPS_DIR.glob(f"{BACKUP_PREFIX}*{BACKUP_SUFFIX}"))
    found += list(BACKUPS_DIR.glob(f"{BACKUP_PREFIX}*{ENCRYPTED_SUFFIX}"))
    # 文件名里的时间戳决定先后（加密与未加密的混在一起也按时间排）
    return sorted(found, key=lambda p: p.name.removesuffix(".enc"))


def _prune(keep: int) -> list[Path]:
    backups = _list_backups()
    removed = backups[:-keep] if keep > 0 else []
    for path in removed:
        try:
            path.unlink()
        except OSError as exc:
            logger.warning("Failed to remove old backup %s: %s", path, exc)
    return removed


def run_backup(
    *,
    keep: int = BACKUP_KEEP,
    include_attachments: bool = BACKUP_INCLUDE_ATTACHMENTS,
    public_key: str = BACKUP_PUBLIC_KEY,
) -> Path:
    """生成一份备份（配置了公钥则加密）并清理旧备份，返回备份文件路径。"""
    with _lock:
        BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        suffix = ENCRYPTED_SUFFIX if public_key else BACKUP_SUFFIX
        final_path = BACKUPS_DIR / f"{BACKUP_PREFIX}{stamp}{suffix}"
        partial_path = final_path.with_name(final_path.name + ".partial")

        with tempfile.TemporaryDirectory(prefix="gc-backup-") as tmp:
            snapshot = Path(tmp) / DB_FILENAME
            _snapshot_database(snapshot)
            # 加密时明文包只写在临时目录里，加密完随临时目录一起删除
            tar_path = Path(tmp) / "backup.tar.gz" if public_key else partial_path
            with tarfile.open(tar_path, "w:gz") as tar:
                tar.add(snapshot, arcname=f"data/{DB_FILENAME}")
                for secret in sorted(SECRETS_DIR.glob("*")):
                    if secret.is_file():
                        tar.add(secret, arcname=f"secrets/{secret.name}")
                if include_attachments and ATTACHMENTS_DIR.is_dir():
                    tar.add(ATTACHMENTS_DIR, arcname="attachments")
            if public_key:
                backup_crypto.encrypt_file(tar_path, partial_path, public_key)

        if os.name != "nt":
            os.chmod(partial_path, stat.S_IRUSR | stat.S_IWUSR)
        # 写完再改名，避免留下半个备份被当成有效备份
        partial_path.replace(final_path)
        removed = _prune(keep)
        logger.info(
            "Backup created %s (%.1f MB, %s), removed %d old backup(s)",
            final_path.name,
            final_path.stat().st_size / 1024 / 1024,
            "encrypted" if public_key else "NOT encrypted",
            len(removed),
        )
        if not public_key:
            logger.warning("BACKUP_PUBLIC_KEY is not set: backup is not encrypted and will not be uploaded offsite")
        return final_path


# ---- 异地上传 -------------------------------------------------------------------
def upload_targets() -> list[str]:
    targets = []
    if BACKUP_UPLOAD_TELEGRAM:
        targets.append("telegram")
    if BACKUP_WEBDAV_URL:
        targets.append("webdav")
    return targets


def _upload_telegram(path: Path) -> None:
    from . import telegram_service

    if not telegram_service.is_configured():
        raise RuntimeError("Telegram 未配置")
    size = path.stat().st_size
    if size > TELEGRAM_MAX_BYTES:
        raise RuntimeError(f"文件 {size / 1024 / 1024:.1f} MB 超过 Telegram 50 MB 上限")
    ok, detail = telegram_service.send_document(
        str(path),
        caption=f"\U0001F512 邮箱客户端加密备份 {path.name}",
        timeout=BACKUP_UPLOAD_TIMEOUT_SEC,
    )
    if not ok:
        raise RuntimeError(detail)


def _upload_webdav(path: Path, base_url: str = BACKUP_WEBDAV_URL) -> None:
    url = base_url if base_url.endswith("/") else base_url + "/"
    url += urllib.parse.quote(path.name)
    headers = {
        "Content-Type": "application/octet-stream",
        "Content-Length": str(path.stat().st_size),
    }
    if BACKUP_WEBDAV_USER or BACKUP_WEBDAV_PASSWORD:
        token = base64.b64encode(f"{BACKUP_WEBDAV_USER}:{BACKUP_WEBDAV_PASSWORD}".encode()).decode()
        headers["Authorization"] = f"Basic {token}"
    with open(path, "rb") as body:
        req = urllib.request.Request(url, data=body, method="PUT", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=BACKUP_UPLOAD_TIMEOUT_SEC) as resp:
                status = resp.status
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"HTTP {exc.code}") from None
    if status not in (200, 201, 204):
        raise RuntimeError(f"HTTP {status}")


_UPLOADERS = {"telegram": _upload_telegram, "webdav": _upload_webdav}


def upload_offsite(path: Path) -> dict[str, Optional[str]]:
    """上传到各个异地目标，返回 {目标: None 表示成功 / 错误信息}。只上传加密备份。"""
    if not path.name.endswith(ENCRYPTED_SUFFIX):
        return {}
    results: dict[str, Optional[str]] = {}
    for target in upload_targets():
        try:
            _UPLOADERS[target](path)
            results[target] = None
            logger.info("Backup %s uploaded to %s", path.name, target)
        except Exception as exc:
            results[target] = str(exc)[:300]
            logger.warning("Backup upload to %s failed: %s", target, exc)
    return results


# ---- 状态（显示在「设置 › 安全」） ---------------------------------------------
def _save_status(status: dict) -> None:
    from ..database import session_scope
    from ..models import AppSetting

    value = json.dumps(status, ensure_ascii=False)
    with session_scope() as db:
        row = db.get(AppSetting, STATUS_KEY)
        if row is None:
            db.add(AppSetting(key=STATUS_KEY, value=value))
        else:
            row.value = value


def last_status() -> Optional[dict]:
    from ..database import session_scope
    from ..models import AppSetting

    with session_scope() as db:
        row = db.get(AppSetting, STATUS_KEY)
        raw = row.value if row else None
    try:
        return json.loads(raw) if raw else None
    except ValueError:
        return None


def backup_and_upload() -> dict:
    """备份 + 异地上传 + 记录审计日志和状态。返回状态字典。"""
    from . import audit

    status: dict = {"at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    try:
        path = run_backup()
    except Exception as exc:
        logger.exception("Backup failed")
        status.update(ok=False, error=str(exc)[:300])
        audit.record("backup_failed", str(exc)[:300], source="system")
        audit.send_alert(f"❌ <b>邮箱客户端备份失败</b>\n{exc}")
        _save_status(status)
        return status

    status.update(ok=True, file=path.name, encrypted=path.name.endswith(ENCRYPTED_SUFFIX),
                  size=path.stat().st_size)
    audit.record("backup_created", f"{path.name}（{'已加密' if status['encrypted'] else '未加密'}）", source="system")
    uploads = upload_offsite(path)
    status["uploads"] = uploads
    for target, error in uploads.items():
        if error is None:
            audit.record("backup_uploaded", f"{target}：{path.name}", source="system")
        else:
            audit.record("backup_upload_failed", f"{target}：{error}", source="system")
    failed = [t for t, e in uploads.items() if e]
    if failed:
        audit.send_alert("⚠️ <b>邮箱客户端异地备份失败</b>\n" + "\n".join(f"{t}：{uploads[t]}" for t in failed))
    _save_status(status)
    return status


def safe_run_backup() -> None:
    """调度器入口：失败只记日志。"""
    try:
        backup_and_upload()
    except Exception:
        logger.exception("Scheduled backup failed")


if __name__ == "__main__":
    from ..config import setup_logging

    setup_logging()
    print(json.dumps(backup_and_upload(), ensure_ascii=False, indent=2))
