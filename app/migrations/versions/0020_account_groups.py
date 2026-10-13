"""账号分组：accounts.group_name

Revision ID: 0020_account_groups
Revises: 0019_otp_code
Create Date: 2026-10-13
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0020_account_groups"
down_revision = "0019_otp_code"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("accounts") as batch_op:
        batch_op.add_column(sa.Column("group_name", sa.String(60), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("accounts") as batch_op:
        batch_op.drop_column("group_name")
