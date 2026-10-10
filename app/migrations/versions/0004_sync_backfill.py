"""同步状态增加历史邮件回填进度

Revision ID: 0004_sync_backfill
Revises: 0003_list_index
Create Date: 2026-10-10
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004_sync_backfill"
down_revision = "0003_list_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("sync_state") as batch_op:
        batch_op.add_column(sa.Column("backfill_before_uid", sa.Integer(), nullable=True))
        batch_op.add_column(
            sa.Column("backfill_done", sa.Boolean(), server_default="0", nullable=False)
        )


def downgrade() -> None:
    with op.batch_alter_table("sync_state") as batch_op:
        batch_op.drop_column("backfill_done")
        batch_op.drop_column("backfill_before_uid")
