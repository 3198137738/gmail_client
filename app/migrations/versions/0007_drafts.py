"""写邮件草稿

Revision ID: 0007_drafts
Revises: 0006_message_starred
Create Date: 2026-10-11
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007_drafts"
down_revision = "0006_message_starred"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "drafts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "account_id",
            sa.Integer(),
            sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("to_addr", sa.Text(), nullable=True),
        sa.Column("cc_addr", sa.Text(), nullable=True),
        sa.Column("bcc_addr", sa.Text(), nullable=True),
        sa.Column("subject", sa.Text(), nullable=True),
        sa.Column("body_text", sa.Text(), nullable=True),
        sa.Column("body_html", sa.Text(), nullable=True),
        sa.Column("in_reply_to", sa.String(998), nullable=True),
        sa.Column("references", sa.Text(), nullable=True),
        sa.Column("forward_message_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_drafts_account_id", "drafts", ["account_id"])
    op.create_index("ix_drafts_updated_at", "drafts", ["updated_at"])


def downgrade() -> None:
    op.drop_index("ix_drafts_updated_at", table_name="drafts")
    op.drop_index("ix_drafts_account_id", table_name="drafts")
    op.drop_table("drafts")
