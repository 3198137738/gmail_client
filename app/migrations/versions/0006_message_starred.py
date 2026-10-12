"""邮件星标

Revision ID: 0006_message_starred
Revises: 0005_outbox_condstore
Create Date: 2026-10-11
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.migrations.fts import create_triggers

revision = "0006_message_starred"
down_revision = "0005_outbox_condstore"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("messages") as batch_op:
        batch_op.add_column(sa.Column("is_starred", sa.Boolean(), server_default="0", nullable=False))
    op.create_index("ix_messages_starred_date", "messages", ["is_starred", "date"])


def downgrade() -> None:
    op.drop_index("ix_messages_starred_date", table_name="messages")
    with op.batch_alter_table("messages") as batch_op:
        batch_op.drop_column("is_starred")
    # batch 操作重建了 messages 表，旧表上的全文索引触发器随之丢失
    create_triggers(op)
