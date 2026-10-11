"""Gmail 标签

Revision ID: 0010_labels
Revises: 0009_message_thread
Create Date: 2026-10-11
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0010_labels"
down_revision = "0009_message_thread"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "labels",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "account_id", sa.Integer(), sa.ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("name", sa.String(512), nullable=False),
        sa.UniqueConstraint("account_id", "name", name="uq_labels_account_name"),
    )
    op.create_table(
        "message_labels",
        sa.Column(
            "message_id", sa.Integer(), sa.ForeignKey("messages.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column(
            "label_id", sa.Integer(), sa.ForeignKey("labels.id", ondelete="CASCADE"), primary_key=True
        ),
    )
    op.create_index("ix_message_labels_label_id", "message_labels", ["label_id"])
    with op.batch_alter_table("sync_state") as batch_op:
        batch_op.add_column(
            sa.Column("labels_backfilled", sa.Boolean(), server_default="0", nullable=False)
        )


def downgrade() -> None:
    with op.batch_alter_table("sync_state") as batch_op:
        batch_op.drop_column("labels_backfilled")
    op.drop_index("ix_message_labels_label_id", table_name="message_labels")
    op.drop_table("message_labels")
    op.drop_table("labels")
