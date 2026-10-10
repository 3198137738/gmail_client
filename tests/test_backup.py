"""自动备份：包含数据库快照与密钥、可恢复、按数量清理。"""
from __future__ import annotations

import sqlite3
import tarfile

from app.services import backup
from tests.helpers import create_account


def test_backup_contains_db_and_secrets(tmp_path, monkeypatch):
    monkeypatch.setattr(backup, "BACKUPS_DIR", tmp_path)
    create_account("backup-check@example.com")

    path = backup.run_backup(keep=5, include_attachments=False)
    assert path.exists() and path.name.endswith(".tar.gz")

    with tarfile.open(path) as tar:
        names = tar.getnames()
        assert "data/gmail_client.db" in names
        assert "secrets/fernet.key" in names
        tar.extractall(tmp_path / "restore", filter="data")

    conn = sqlite3.connect(tmp_path / "restore" / "data" / "gmail_client.db")
    emails = {row[0] for row in conn.execute("SELECT email FROM accounts")}
    conn.close()
    assert "backup-check@example.com" in emails


def test_old_backups_are_pruned(tmp_path, monkeypatch):
    monkeypatch.setattr(backup, "BACKUPS_DIR", tmp_path)
    for i in range(5):
        (tmp_path / f"gmail-client-2026010{i}-000000.tar.gz").write_bytes(b"old")

    newest = backup.run_backup(keep=3, include_attachments=False)
    remaining = sorted(p.name for p in tmp_path.glob("gmail-client-*.tar.gz"))
    assert len(remaining) == 3
    assert newest.name in remaining
    assert "gmail-client-20260100-000000.tar.gz" not in remaining
