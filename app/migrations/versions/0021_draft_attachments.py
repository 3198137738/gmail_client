"""草稿附件（draft_attachments）；账号的发信连接方式（accounts.smtp_starttls）

Revision ID: 0021_draft_attachments
Revises: 0020_account_groups
Create Date: 2026-11-09
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0021_draft_attachments"
down_revision = "0020_account_groups"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "draft_attachments",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("draft_id", sa.Integer(), sa.ForeignKey("drafts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("filename", sa.String(512), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("mime_type", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_draft_attachments_draft_id", "draft_attachments", ["draft_id"])
    with op.batch_alter_table("accounts") as batch_op:
        batch_op.add_column(sa.Column("smtp_starttls", sa.Boolean(), nullable=False, server_default="0"))


def downgrade() -> None:
    with op.batch_alter_table("accounts") as batch_op:
        batch_op.drop_column("smtp_starttls")
    op.drop_index("ix_draft_attachments_draft_id", table_name="draft_attachments")
    op.drop_table("draft_attachments")
