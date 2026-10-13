"""签名：accounts.signature_html

Revision ID: 0017_signatures
Revises: 0016_mail_templates
Create Date: 2026-10-13
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0017_signatures"
down_revision = "0016_mail_templates"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("accounts") as batch_op:
        batch_op.add_column(sa.Column("signature_html", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("accounts") as batch_op:
        batch_op.drop_column("signature_html")
