"""baseline：引入 Alembic 时的完整表结构

Revision ID: 0001_baseline
Revises:
Create Date: 2026-10-10

新库：按下面的定义建表建索引。
旧库（引入 Alembic 之前创建、没有 alembic_version 表）：已存在的表和索引
会跳过，并补上旧版本缺少的列，再执行一次附件目录布局迁移——这些原本是
每次启动都会跑的手写迁移（database._run_migrations），现在只执行一次。
"""
from __future__ import annotations

import logging
from pathlib import Path

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text

from app.config import ATTACHMENTS_DIR

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")


def _table_exists(name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(name)


def _column_names(table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def _create_table(name: str, *columns) -> None:
    if not _table_exists(name):
        op.create_table(name, *columns)


def _create_index(name: str, table: str, columns: list[str], unique: bool = False) -> None:
    op.create_index(name, table, columns, unique=unique, if_not_exists=True)


def _add_legacy_columns() -> None:
    """旧库可能缺少的列（原 database._run_migrations 的内容）。"""
    conn = op.get_bind()
    if "folder_kind" not in _column_names("messages"):
        logger.info("Migration: adding messages.folder_kind column")
        conn.execute(text(
            "ALTER TABLE messages ADD COLUMN folder_kind VARCHAR(32) NOT NULL DEFAULT 'inbox'"
        ))
        conn.execute(text("UPDATE messages SET folder_kind = 'inbox' WHERE folder = 'INBOX'"))
        conn.execute(text(
            "UPDATE messages SET folder_kind = 'sent' "
            "WHERE folder LIKE '%Sent%' OR folder LIKE '%已发送%'"
        ))
    if "notify_enabled" not in _column_names("accounts"):
        logger.info("Migration: adding accounts.notify_enabled column")
        conn.execute(text(
            "ALTER TABLE accounts ADD COLUMN notify_enabled BOOLEAN NOT NULL DEFAULT 1"
        ))
    if "is_permanent_blocked" not in _column_names("login_throttle"):
        logger.info("Migration: adding login_throttle.is_permanent_blocked column")
        conn.execute(text(
            "ALTER TABLE login_throttle ADD COLUMN is_permanent_blocked BOOLEAN NOT NULL DEFAULT 0"
        ))
    if "display_name" not in _column_names("attachments"):
        logger.info("Migration: adding attachments.display_name column")
        conn.execute(text("ALTER TABLE attachments ADD COLUMN display_name VARCHAR(512)"))
        # 旧数据用磁盘文件名回填，新同步的邮件会保存原始文件名
        conn.execute(text("UPDATE attachments SET display_name = filename WHERE display_name IS NULL"))


def upgrade() -> None:
    _create_table("accounts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=True),
        sa.Column("encrypted_password", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("notify_enabled", sa.Boolean(), server_default="1", nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
    )
    _create_table("login_throttle",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("ip_address", sa.String(length=255), nullable=False),
        sa.Column("failed_attempts", sa.Integer(), nullable=False),
        sa.Column("last_failed_at", sa.DateTime(), nullable=True),
        sa.Column("is_permanent_blocked", sa.Boolean(), nullable=False),
        sa.Column("blocked_until", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("ip_address"),
    )

    _create_table("blocked_senders",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("sender_email", sa.String(length=320), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("account_id", "sender_email", name="uq_blocked_sender_account_email"),
    )

    _create_table("messages",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("folder", sa.String(length=255), nullable=False),
        sa.Column("folder_kind", sa.String(length=32), server_default="inbox", nullable=False),
        sa.Column("uid", sa.Integer(), nullable=False),
        sa.Column("message_id", sa.String(length=998), nullable=True),
        sa.Column("subject", sa.Text(), nullable=True),
        sa.Column("from_addr", sa.Text(), nullable=True),
        sa.Column("to_addr", sa.Text(), nullable=True),
        sa.Column("cc_addr", sa.Text(), nullable=True),
        sa.Column("bcc_addr", sa.Text(), nullable=True),
        sa.Column("date", sa.DateTime(), nullable=True),
        sa.Column("body_text", sa.Text(), nullable=True),
        sa.Column("body_html", sa.Text(), nullable=True),
        sa.Column("is_read", sa.Boolean(), nullable=False),
        sa.Column("has_attachments", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("account_id", "folder", "uid", name="uq_messages_account_folder_uid"),
    )

    _create_table("sent_messages",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("to_addr", sa.Text(), nullable=False),
        sa.Column("cc_addr", sa.Text(), nullable=True),
        sa.Column("bcc_addr", sa.Text(), nullable=True),
        sa.Column("subject", sa.Text(), nullable=True),
        sa.Column("body_text", sa.Text(), nullable=True),
        sa.Column("body_html", sa.Text(), nullable=True),
        sa.Column("message_id", sa.String(length=998), nullable=True),
        sa.Column("sent_at", sa.DateTime(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    _create_table("sync_state",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("folder", sa.String(length=255), nullable=False),
        sa.Column("last_uid", sa.Integer(), nullable=False),
        sa.Column("uidvalidity", sa.Integer(), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("account_id", "folder", name="uq_sync_state_account_folder"),
    )
    _create_table("trusted_senders",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("sender_email", sa.String(length=320), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("account_id", "sender_email", name="uq_trusted_sender_account_email"),
    )

    _create_table("attachments",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("message_id", sa.Integer(), nullable=False),
        sa.Column("filename", sa.String(length=512), nullable=False),
        sa.Column("display_name", sa.String(length=512), nullable=True),
        sa.Column("mime_type", sa.String(length=255), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("is_inline", sa.Boolean(), nullable=False),
        sa.Column("content_id", sa.String(length=255), nullable=True),
        sa.Column("file_path", sa.Text(), nullable=False),
        sa.Column("public_url", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["message_id"], ["messages.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    _add_legacy_columns()

    _create_index("ix_login_throttle_blocked_until", "login_throttle", ["blocked_until"], unique=False)
    _create_index("ix_blocked_senders_account_id", "blocked_senders", ["account_id"], unique=False)
    _create_index("ix_blocked_senders_account_sender", "blocked_senders", ["account_id", "sender_email"], unique=False)
    _create_index("ix_messages_account_date", "messages", ["account_id", "date"], unique=False)
    _create_index("ix_messages_account_id", "messages", ["account_id"], unique=False)
    _create_index("ix_messages_date", "messages", ["date"], unique=False)
    _create_index("ix_messages_folder_kind", "messages", ["folder_kind"], unique=False)
    _create_index("ix_messages_kind_date", "messages", ["folder_kind", "date"], unique=False)
    _create_index("ix_messages_kind_unread", "messages", ["folder_kind", "is_read"], unique=False)
    _create_index("ix_messages_message_id", "messages", ["message_id"], unique=False)
    _create_index("ix_messages_subject", "messages", ["subject"], unique=False)
    _create_index("ix_sent_messages_account_id", "sent_messages", ["account_id"], unique=False)
    _create_index("ix_sent_messages_sent_at", "sent_messages", ["sent_at"], unique=False)
    _create_index("ix_trusted_senders_account_id", "trusted_senders", ["account_id"], unique=False)
    _create_index("ix_attachments_content_id", "attachments", ["content_id"], unique=False)
    _create_index("ix_attachments_message_id", "attachments", ["message_id"], unique=False)

    _migrate_attachment_paths_to_folder_kind(op.get_bind())


def downgrade() -> None:
    raise NotImplementedError("baseline 不支持降级")


def _migrate_attachment_paths_to_folder_kind(conn) -> None:
    """Move legacy attachments/{account_id}/{uid}/... into folder_kind subdir.

    Old layout: ``attachments/<account_id>/<uid>/{inline|files}/``
    New layout: ``attachments/<account_id>/<folder_kind>/<uid>/{inline|files}/``

    Also rewrites ``attachments.file_path``, ``attachments.public_url`` and the
    image src URLs embedded in ``messages.body_html`` to match.
    """
    rows = conn.execute(
        text(
            """
            SELECT a.id           AS att_id,
                   a.file_path    AS file_path,
                   a.public_url   AS public_url,
                   m.id           AS msg_id,
                   m.account_id   AS account_id,
                   m.uid          AS uid,
                   m.folder_kind  AS folder_kind
            FROM attachments a
            JOIN messages m ON m.id = a.message_id
            """
        )
    ).mappings().fetchall()

    if not rows:
        return

    moved_dirs: set[Path] = set()
    moved_messages: set[int] = set()
    for row in rows:
        kind = row["folder_kind"] or "inbox"
        account_id = row["account_id"]
        uid = row["uid"]
        old_url_prefix = f"/attachments/{account_id}/{uid}/"
        new_url_prefix = f"/attachments/{account_id}/{kind}/{uid}/"

        public_url = row["public_url"] or ""
        if not public_url.startswith(old_url_prefix):
            continue

        new_public_url = new_url_prefix + public_url[len(old_url_prefix):]

        old_dir = ATTACHMENTS_DIR / str(account_id) / str(uid)
        new_dir = ATTACHMENTS_DIR / str(account_id) / kind / str(uid)
        if old_dir.is_dir() and old_dir not in moved_dirs:
            new_dir.parent.mkdir(parents=True, exist_ok=True)
            try:
                if not new_dir.exists():
                    old_dir.rename(new_dir)
                else:
                    for child in old_dir.iterdir():
                        target = new_dir / child.name
                        if target.exists():
                            continue
                        child.rename(target)
                    try:
                        old_dir.rmdir()
                    except OSError:
                        pass
                moved_dirs.add(old_dir)
            except OSError as exc:
                logger.warning("Migration: failed to move %s -> %s: %s", old_dir, new_dir, exc)

        old_file_path = row["file_path"] or ""
        new_file_path = old_file_path
        try:
            normalized = Path(old_file_path).resolve()
            base = ATTACHMENTS_DIR.resolve()
            rel = normalized.relative_to(base)
            parts = rel.parts
            if len(parts) >= 2 and parts[0] == str(account_id) and parts[1] == str(uid):
                new_rel = Path(str(account_id), kind, *parts[1:])
                new_file_path = str((base / new_rel))
        except (ValueError, OSError):
            pass

        conn.execute(
            text("UPDATE attachments SET file_path = :fp, public_url = :pu WHERE id = :id"),
            {"fp": new_file_path, "pu": new_public_url, "id": row["att_id"]},
        )

        if row["msg_id"] not in moved_messages:
            conn.execute(
                text(
                    """
                    UPDATE messages
                    SET body_html = REPLACE(body_html, :old_prefix, :new_prefix)
                    WHERE id = :mid AND body_html IS NOT NULL
                    """
                ),
                {"old_prefix": old_url_prefix, "new_prefix": new_url_prefix, "mid": row["msg_id"]},
            )
            moved_messages.add(row["msg_id"])

    if moved_dirs:
        logger.info("Migration: relocated %d attachment dirs into folder_kind layout", len(moved_dirs))


