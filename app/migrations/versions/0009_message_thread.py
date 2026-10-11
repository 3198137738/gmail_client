"""邮件会话 id（Gmail X-GM-THRID）

Revision ID: 0009_message_thread
Revises: 0008_app_settings
Create Date: 2026-10-11
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0009_message_thread"
down_revision = "0008_app_settings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("messages") as batch_op:
        batch_op.add_column(sa.Column("thread_id", sa.BigInteger(), nullable=True))
    op.create_index("ix_messages_account_thread", "messages", ["account_id", "thread_id"])


def downgrade() -> None:
    op.drop_index("ix_messages_account_thread", table_name="messages")
    with op.batch_alter_table("messages") as batch_op:
        batch_op.drop_column("thread_id")
