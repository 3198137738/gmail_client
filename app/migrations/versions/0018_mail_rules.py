"""自动规则：mail_rules 表

Revision ID: 0018_mail_rules
Revises: 0017_signatures
Create Date: 2026-10-13
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0018_mail_rules"
down_revision = "0017_signatures"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mail_rules",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(120), nullable=False, server_default=""),
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("accounts.id", ondelete="CASCADE"), nullable=True),
        sa.Column("field", sa.String(16), nullable=False, server_default="from"),
        sa.Column("contains", sa.String(320), nullable=False),
        sa.Column("mark_read", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("star", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("label_name", sa.String(512), nullable=True),
        sa.Column("delete", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("hits", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_mail_rules_account_id", "mail_rules", ["account_id"])


def downgrade() -> None:
    op.drop_index("ix_mail_rules_account_id", table_name="mail_rules")
    op.drop_table("mail_rules")
