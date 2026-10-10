"""邮件全文搜索：FTS5（trigram）索引

Revision ID: 0002_messages_fts
Revises: 0001_baseline
Create Date: 2026-10-10

* 新建外部内容表 messages_fts，索引 subject / from_addr / to_addr / body_text；
  trigram 分词支持中文任意子串匹配，与原先 LIKE '%q%' 的语义一致。
* 用触发器与 messages 表保持同步；只在这四列变化时更新索引，
  标记已读等操作不会触发重建。
* 删除对 LIKE '%q%' 无效的 ix_messages_subject 索引。
"""
from __future__ import annotations

from alembic import op

revision = "0002_messages_fts"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None

_COLS = "subject, from_addr, to_addr, body_text"
_NEW = "new.subject, new.from_addr, new.to_addr, new.body_text"
_OLD = "old.subject, old.from_addr, old.to_addr, old.body_text"


def upgrade() -> None:
    op.execute(
        f"""
        CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
            {_COLS},
            content='messages',
            content_rowid='id',
            tokenize='trigram'
        )
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER IF NOT EXISTS messages_fts_ai AFTER INSERT ON messages BEGIN
            INSERT INTO messages_fts(rowid, {_COLS}) VALUES (new.id, {_NEW});
        END
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER IF NOT EXISTS messages_fts_ad AFTER DELETE ON messages BEGIN
            INSERT INTO messages_fts(messages_fts, rowid, {_COLS})
            VALUES ('delete', old.id, {_OLD});
        END
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER IF NOT EXISTS messages_fts_au
        AFTER UPDATE OF {_COLS} ON messages BEGIN
            INSERT INTO messages_fts(messages_fts, rowid, {_COLS})
            VALUES ('delete', old.id, {_OLD});
            INSERT INTO messages_fts(rowid, {_COLS}) VALUES (new.id, {_NEW});
        END
        """
    )
    # 为已有邮件建立索引
    op.execute("INSERT INTO messages_fts(messages_fts) VALUES ('rebuild')")
    op.execute("DROP INDEX IF EXISTS ix_messages_subject")


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS messages_fts_ai")
    op.execute("DROP TRIGGER IF EXISTS messages_fts_ad")
    op.execute("DROP TRIGGER IF EXISTS messages_fts_au")
    op.execute("DROP TABLE IF EXISTS messages_fts")
    op.create_index("ix_messages_subject", "messages", ["subject"])
