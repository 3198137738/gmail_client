"""稍后提醒：messages.snoozed_until

Revision ID: 0015_snooze
Revises: 0014_scheduled_sends
Create Date: 2026-10-13
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0015_snooze"
down_revision = "0014_scheduled_sends"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("messages") as batch_op:
        batch_op.add_column(sa.Column("snoozed_until", sa.DateTime(), nullable=True))
    op.create_index("ix_messages_snoozed_until", "messages", ["snoozed_until"])


def downgrade() -> None:
    op.drop_index("ix_messages_snoozed_until", table_name="messages")
    with op.batch_alter_table("messages") as batch_op:
        batch_op.drop_column("snoozed_until")
