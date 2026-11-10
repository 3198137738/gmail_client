"""删除时本地立即移入「已删除」：messages.moved_at / moved_from_kind / purge_pending

Revision ID: 0023_message_local_trash_move
Revises: 0022_message_body_pending
Create Date: 2026-11-10
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0023_message_local_trash_move"
down_revision = "0022_message_body_pending"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("messages") as batch_op:
        batch_op.add_column(sa.Column("moved_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("moved_from_kind", sa.String(32), nullable=True))
        batch_op.add_column(sa.Column("purge_pending", sa.Boolean(), nullable=False, server_default="0"))


def downgrade() -> None:
    with op.batch_alter_table("messages") as batch_op:
        batch_op.drop_column("purge_pending")
        batch_op.drop_column("moved_from_kind")
        batch_op.drop_column("moved_at")
