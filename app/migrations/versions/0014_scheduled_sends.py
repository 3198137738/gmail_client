"""定时发送：scheduled_sends 表

Revision ID: 0014_scheduled_sends
Revises: 0013_received_dates
Create Date: 2026-10-13
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0014_scheduled_sends"
down_revision = "0013_received_dates"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "scheduled_sends",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("to_addr", sa.Text(), nullable=False, server_default=""),
        sa.Column("cc_addr", sa.Text(), nullable=True),
        sa.Column("bcc_addr", sa.Text(), nullable=True),
        sa.Column("subject", sa.Text(), nullable=True),
        sa.Column("body_text", sa.Text(), nullable=True),
        sa.Column("body_html", sa.Text(), nullable=True),
        sa.Column("in_reply_to", sa.String(998), nullable=True),
        sa.Column("references", sa.Text(), nullable=True),
        sa.Column("forward_message_id", sa.Integer(), nullable=True),
        sa.Column("use_ssl", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("send_at", sa.DateTime(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("attachment_names", sa.Text(), nullable=True),
        sa.Column("attachments_dir", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_scheduled_sends_account_id", "scheduled_sends", ["account_id"])
    op.create_index("ix_scheduled_sends_send_at", "scheduled_sends", ["send_at"])


def downgrade() -> None:
    op.drop_index("ix_scheduled_sends_send_at", table_name="scheduled_sends")
    op.drop_index("ix_scheduled_sends_account_id", table_name="scheduled_sends")
    op.drop_table("scheduled_sends")
