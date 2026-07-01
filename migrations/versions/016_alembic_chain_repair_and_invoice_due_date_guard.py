"""Alembic chain repair and invoice due-date guard.

Revision ID: 016
Revises: 015
Create Date: 2026-06-18
"""
from __future__ import annotations

from datetime import date
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '016'
down_revision: Union[str, None] = '015'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(bind, table_name: str) -> bool:
    return table_name in sa.inspect(bind).get_table_names()


def _column_exists(bind, table_name: str, column_name: str) -> bool:
    if not _table_exists(bind, table_name):
        return False
    return any(col['name'] == column_name for col in sa.inspect(bind).get_columns(table_name))


def upgrade() -> None:
    bind = op.get_bind()

    if _table_exists(bind, 'invoice'):
        # Keep the live schema aligned with the invoice service during the
        # transition away from legacy invoice rows.
        if _column_exists(bind, 'invoice', 'due_date'):
            op.alter_column(
                'invoice',
                'due_date',
                existing_type=sa.Date(),
                nullable=False,
                server_default=sa.text('(CURRENT_DATE + 14)'),
            )
            op.execute(sa.text("""
                UPDATE invoice
                SET due_date = COALESCE(due_date, COALESCE(issued_at::date, CURRENT_DATE) + 14)
            """))

        if _column_exists(bind, 'invoice', 'customer_name'):
            op.alter_column(
                'invoice',
                'customer_name',
                existing_type=sa.String(length=120),
                nullable=False,
                server_default=sa.text("''"),
            )
            op.execute(sa.text("""
                UPDATE invoice
                SET customer_name = COALESCE(NULLIF(customer_name, ''), NULLIF(customer_name_snapshot, ''), 'Customer')
            """))

        if _column_exists(bind, 'invoice', 'customer_email'):
            op.alter_column(
                'invoice',
                'customer_email',
                existing_type=sa.String(length=320),
                nullable=False,
                server_default=sa.text("''"),
            )
            op.execute(sa.text("""
                UPDATE invoice
                SET customer_email = COALESCE(NULLIF(customer_email, ''), NULLIF(customer_email_snapshot, ''), '')
            """))

        if _column_exists(bind, 'invoice', 'customer_name_snapshot'):
            op.execute(sa.text("""
                UPDATE invoice
                SET customer_name_snapshot = COALESCE(NULLIF(customer_name_snapshot, ''), NULLIF(customer_name, ''), 'Customer')
            """))

        if _column_exists(bind, 'invoice', 'customer_email_snapshot'):
            op.execute(sa.text("""
                UPDATE invoice
                SET customer_email_snapshot = COALESCE(NULLIF(customer_email_snapshot, ''), NULLIF(customer_email, ''), '')
            """))


def downgrade() -> None:
    bind = op.get_bind()
    if _table_exists(bind, 'invoice'):
        if _column_exists(bind, 'invoice', 'customer_email'):
            op.alter_column(
                'invoice',
                'customer_email',
                existing_type=sa.String(length=320),
                nullable=False,
                server_default=None,
            )
        if _column_exists(bind, 'invoice', 'customer_name'):
            op.alter_column(
                'invoice',
                'customer_name',
                existing_type=sa.String(length=120),
                nullable=False,
                server_default=None,
            )
        if _column_exists(bind, 'invoice', 'due_date'):
            op.alter_column(
                'invoice',
                'due_date',
                existing_type=sa.Date(),
                nullable=False,
                server_default=None,
            )
