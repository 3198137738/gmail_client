"""邮件列表：账号 + 文件夹 + 日期联合索引

Revision ID: 0003_list_index
Revises: 0002_messages_fts
Create Date: 2026-10-10

选中某个账号浏览收件箱等文件夹时，过滤和按日期排序/游标分页
都能直接走这个索引。
"""
from __future__ import annotations

from alembic import op

revision = "0003_list_index"
down_revision = "0002_messages_fts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_messages_account_kind_date",
        "messages",
        ["account_id", "folder_kind", "date"],
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index("ix_messages_account_kind_date", table_name="messages")
