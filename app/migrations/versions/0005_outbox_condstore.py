"""远程操作 outbox 表；同步状态增加 CONDSTORE 进度与删除比对时间

Revision ID: 0005_outbox_condstore
Revises: 0004_sync_backfill
Create Date: 2026-10-11
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005_outbox_condstore"
down_revision = "0004_sync_backfill"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pending_ops",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "account_id",
            sa.Integer(),
            sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("folder", sa.String(255), nullable=False),
        sa.Column("uidvalidity", sa.Integer(), nullable=True),
        sa.Column("op", sa.String(32), nullable=False),
        sa.Column("uids", sa.Text(), nullable=False),
        sa.Column("arg", sa.String(255), nullable=True),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_pending_ops_account_due", "pending_ops", ["account_id", "next_attempt_at"])
    with op.batch_alter_table("sync_state") as batch_op:
        batch_op.add_column(sa.Column("highestmodseq", sa.BigInteger(), nullable=True))
        batch_op.add_column(sa.Column("reconciled_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("sync_state") as batch_op:
        batch_op.drop_column("reconciled_at")
        batch_op.drop_column("highestmodseq")
    op.drop_index("ix_pending_ops_account_due", table_name="pending_ops")
    op.drop_table("pending_ops")
