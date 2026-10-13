"""验证码：messages.otp_code

Revision ID: 0019_otp_code
Revises: 0018_mail_rules
Create Date: 2026-10-13
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0019_otp_code"
down_revision = "0018_mail_rules"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("messages") as batch_op:
        batch_op.add_column(sa.Column("otp_code", sa.String(16), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("messages") as batch_op:
        batch_op.drop_column("otp_code")
