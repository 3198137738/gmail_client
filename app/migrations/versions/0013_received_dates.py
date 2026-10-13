"""sync_state 增加 dates_backfilled：邮件时间改用 Gmail 收到时间（INTERNALDATE）

升级前入库的邮件，时间取自发件方写的 Date 头（可能不准，曾见过 2611 年的）。
每个文件夹下次同步时把已入库邮件的时间补取为 INTERNALDATE，完成后置为 true。

Revision ID: 0013_received_dates
Revises: 0012_ip_block_source
Create Date: 2026-10-13
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0013_received_dates"
down_revision = "0012_ip_block_source"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("sync_state") as batch_op:
        batch_op.add_column(
            sa.Column("dates_backfilled", sa.Boolean(), server_default="0", nullable=False)
        )


def downgrade() -> None:
    with op.batch_alter_table("sync_state") as batch_op:
        batch_op.drop_column("dates_backfilled")
