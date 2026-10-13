"""邮件模板：mail_templates 表

Revision ID: 0016_mail_templates
Revises: 0015_snooze
Create Date: 2026-10-13
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0016_mail_templates"
down_revision = "0015_snooze"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mail_templates",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("body_html", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("mail_templates")
