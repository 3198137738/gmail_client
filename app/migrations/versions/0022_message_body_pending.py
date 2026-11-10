"""新邮件头部先行：messages.body_pending（正文还在下载）

Revision ID: 0022_message_body_pending
Revises: 0021_draft_attachments
Create Date: 2026-11-10
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0022_message_body_pending"
down_revision = "0021_draft_attachments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("messages") as batch_op:
        batch_op.add_column(sa.Column("body_pending", sa.Boolean(), nullable=False, server_default="0"))


def downgrade() -> None:
    with op.batch_alter_table("messages") as batch_op:
        batch_op.drop_column("body_pending")
