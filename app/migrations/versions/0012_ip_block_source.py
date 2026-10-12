"""login_throttle 增加 blocked_by：区分密码错误自动封禁与手动拉黑

Revision ID: 0012_ip_block_source
Revises: 0011_sessions_audit
Create Date: 2026-10-12
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0012_ip_block_source"
down_revision = "0011_sessions_audit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("login_throttle") as batch_op:
        batch_op.add_column(sa.Column("blocked_by", sa.String(16), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("login_throttle") as batch_op:
        batch_op.drop_column("blocked_by")
