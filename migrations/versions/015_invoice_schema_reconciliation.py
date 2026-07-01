"""Invoice schema reconciliation.

Revision ID: 015
Revises: 014
Create Date: 2026-06-18
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '015'
down_revision: Union[str, None] = '014'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(bind, table_name: str) -> bool:
    return table_name in sa.inspect(bind).get_table_names()


def _column_exists(bind, table_name: str, column_name: str) -> bool:
    if not _table_exists(bind, table_name):
        return False
    return any(col['name'] == column_name for col in sa.inspect(bind).get_columns(table_name))


def _ensure_column(table_name: str, column_name: str, column: sa.Column) -> None:
    bind = op.get_bind()
    if not _column_exists(bind, table_name, column_name):
        op.add_column(table_name, column)


def upgrade() -> None:
    bind = op.get_bind()

    if not _table_exists(bind, 'invoice'):
        op.create_table(
            'invoice',
            sa.Column('invoice_id', sa.Integer(), primary_key=True),
            sa.Column('tenant_id', sa.Integer(), nullable=True),
            sa.Column('job_id', sa.Integer(), nullable=False),
            sa.Column('invoice_number', sa.String(length=40), nullable=False),
            sa.Column('customer_id', sa.Integer(), nullable=False),
            sa.Column('customer_name', sa.String(length=120), nullable=False, server_default=''),
            sa.Column('customer_email', sa.String(length=320), nullable=False, server_default=''),
            sa.Column('customer_name_snapshot', sa.String(length=120), nullable=True),
            sa.Column('customer_email_snapshot', sa.String(length=320), nullable=True),
            sa.Column('vehicle_snapshot', sa.JSON(), nullable=True),
            sa.Column('line_items_snapshot', sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
            sa.Column('notes_snapshot', sa.Text(), nullable=True),
            sa.Column('currency', sa.String(length=8), nullable=False, server_default='ZAR'),
            sa.Column('subtotal', sa.Numeric(12, 2), nullable=False, server_default='0'),
            sa.Column('tax_rate', sa.Numeric(8, 4), nullable=False, server_default='0'),
            sa.Column('tax_amount', sa.Numeric(12, 2), nullable=False, server_default='0'),
            sa.Column('total_amount', sa.Numeric(12, 2), nullable=False, server_default='0'),
            sa.Column('status', sa.String(length=20), nullable=False, server_default='draft'),
            sa.Column('issued_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
            sa.Column('due_date', sa.Date(), nullable=False, server_default=sa.text('(CURRENT_DATE + 14)')),
            sa.Column('pdf_generated_at', sa.DateTime(), nullable=True),
            sa.Column('sent_at', sa.DateTime(), nullable=True),
            sa.Column('paid_at', sa.DateTime(), nullable=True),
            sa.Column('email_subject', sa.String(length=255), nullable=True),
            sa.Column('email_body', sa.Text(), nullable=True),
            sa.Column('line_items_json', sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
            sa.Column('delivery_status', sa.String(length=20), nullable=False, server_default='pending'),
            sa.Column('email_error', sa.Text(), nullable=True),
            sa.Column('is_email_enabled', sa.Boolean(), nullable=False, server_default=sa.text('false')),
            sa.Column('is_email_sent', sa.Boolean(), nullable=False, server_default=sa.text('false')),
            sa.ForeignKeyConstraint(['tenant_id'], ['tenant.tenant_id'], onupdate='CASCADE'),
            sa.ForeignKeyConstraint(['job_id'], ['job.job_id'], onupdate='CASCADE'),
            sa.ForeignKeyConstraint(['customer_id'], ['customer.customer_id'], onupdate='CASCADE'),
            sa.UniqueConstraint('tenant_id', 'invoice_number', name='uq_invoice_tenant_number'),
            sa.UniqueConstraint('job_id', name='uq_invoice_job'),
        )
        op.create_index(op.f('ix_invoice_tenant_id'), 'invoice', ['tenant_id'], unique=False)
        op.create_index(op.f('ix_invoice_job_id'), 'invoice', ['job_id'], unique=False)
        op.create_index(op.f('ix_invoice_invoice_number'), 'invoice', ['invoice_number'], unique=False)
        op.create_index(op.f('ix_invoice_customer_id'), 'invoice', ['customer_id'], unique=False)
        op.create_index(op.f('ix_invoice_status'), 'invoice', ['status'], unique=False)
        return

    # Add missing compatibility columns first.
    _ensure_column('invoice', 'tax_rate', sa.Column('tax_rate', sa.Numeric(8, 4), nullable=False, server_default='0'))
    _ensure_column('invoice', 'customer_name_snapshot', sa.Column('customer_name_snapshot', sa.String(length=120), nullable=True))
    _ensure_column('invoice', 'customer_email_snapshot', sa.Column('customer_email_snapshot', sa.String(length=320), nullable=True))
    _ensure_column('invoice', 'vehicle_snapshot', sa.Column('vehicle_snapshot', sa.JSON(), nullable=True))
    _ensure_column('invoice', 'line_items_snapshot', sa.Column('line_items_snapshot', sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")))
    _ensure_column('invoice', 'notes_snapshot', sa.Column('notes_snapshot', sa.Text(), nullable=True))
    _ensure_column('invoice', 'pdf_generated_at', sa.Column('pdf_generated_at', sa.DateTime(), nullable=True))
    _ensure_column('invoice', 'is_email_sent', sa.Column('is_email_sent', sa.Boolean(), nullable=False, server_default=sa.text('false')))

    # Keep legacy columns predictable even when the application forgets to populate them.
    if _column_exists(bind, 'invoice', 'customer_name'):
        op.alter_column('invoice', 'customer_name', existing_type=sa.String(length=120), server_default=sa.text("''"))
    if _column_exists(bind, 'invoice', 'customer_email'):
        op.alter_column('invoice', 'customer_email', existing_type=sa.String(length=320), server_default=sa.text("''"))
    if _column_exists(bind, 'invoice', 'currency'):
        op.alter_column('invoice', 'currency', existing_type=sa.String(length=8), server_default=sa.text("'ZAR'"))
    if _column_exists(bind, 'invoice', 'subtotal'):
        op.alter_column('invoice', 'subtotal', existing_type=sa.Numeric(12, 2), server_default=sa.text('0'))
    if _column_exists(bind, 'invoice', 'tax_amount'):
        op.alter_column('invoice', 'tax_amount', existing_type=sa.Numeric(12, 2), server_default=sa.text('0'))
    if _column_exists(bind, 'invoice', 'total_amount'):
        op.alter_column('invoice', 'total_amount', existing_type=sa.Numeric(12, 2), server_default=sa.text('0'))
    if _column_exists(bind, 'invoice', 'status'):
        op.alter_column('invoice', 'status', existing_type=sa.String(length=20), server_default=sa.text("'draft'"))
    if _column_exists(bind, 'invoice', 'issued_at'):
        op.alter_column('invoice', 'issued_at', existing_type=sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'))
    if _column_exists(bind, 'invoice', 'due_date'):
        op.alter_column('invoice', 'due_date', existing_type=sa.Date(), server_default=sa.text('(CURRENT_DATE + 14)'))
    if _column_exists(bind, 'invoice', 'delivery_status'):
        op.alter_column('invoice', 'delivery_status', existing_type=sa.String(length=20), server_default=sa.text("'pending'"))
    if _column_exists(bind, 'invoice', 'is_email_enabled'):
        op.alter_column('invoice', 'is_email_enabled', existing_type=sa.Boolean(), server_default=sa.text('false'))

    # Backfill rows that were created during earlier partially deployed releases.
    op.execute(sa.text("""
        UPDATE invoice i
        SET
            customer_name = COALESCE(NULLIF(i.customer_name, ''), NULLIF(i.customer_name_snapshot, ''), TRIM(COALESCE(c.first_name, '') || ' ' || COALESCE(c.family_name, '')), 'Customer'),
            customer_email = COALESCE(NULLIF(i.customer_email, ''), NULLIF(i.customer_email_snapshot, ''), c.email, ''),
            customer_name_snapshot = COALESCE(NULLIF(i.customer_name_snapshot, ''), NULLIF(i.customer_name, ''), TRIM(COALESCE(c.first_name, '') || ' ' || COALESCE(c.family_name, '')), 'Customer'),
            customer_email_snapshot = COALESCE(NULLIF(i.customer_email_snapshot, ''), NULLIF(i.customer_email, ''), c.email, ''),
            due_date = COALESCE(i.due_date, COALESCE(i.issued_at::date, CURRENT_DATE) + 14),
            tax_rate = COALESCE(i.tax_rate, 0),
            line_items_snapshot = COALESCE(i.line_items_snapshot, i.line_items_json, '[]'::json),
            line_items_json = COALESCE(i.line_items_json, i.line_items_snapshot, '[]'::json),
            notes_snapshot = COALESCE(i.notes_snapshot, ''),
            vehicle_snapshot = COALESCE(i.vehicle_snapshot, '{}'::json),
            is_email_enabled = COALESCE(i.is_email_enabled, false),
            is_email_sent = COALESCE(i.is_email_sent, false)
        FROM customer c
        WHERE i.customer_id = c.customer_id
    """))

    # Keep invoice rows valid even if they do not match a current customer record.
    op.execute(sa.text("""
        UPDATE invoice
        SET
            customer_name = COALESCE(NULLIF(customer_name, ''), 'Customer'),
            customer_email = COALESCE(NULLIF(customer_email, ''), ''),
            customer_name_snapshot = COALESCE(NULLIF(customer_name_snapshot, ''), NULLIF(customer_name, ''), 'Customer'),
            customer_email_snapshot = COALESCE(NULLIF(customer_email_snapshot, ''), NULLIF(customer_email, ''), ''),
            due_date = COALESCE(due_date, COALESCE(issued_at::date, CURRENT_DATE) + 14),
            tax_rate = COALESCE(tax_rate, 0),
            line_items_snapshot = COALESCE(line_items_snapshot, '[]'::json),
            line_items_json = COALESCE(line_items_json, '[]'::json),
            notes_snapshot = COALESCE(notes_snapshot, ''),
            vehicle_snapshot = COALESCE(vehicle_snapshot, '{}'::json),
            is_email_enabled = COALESCE(is_email_enabled, false),
            is_email_sent = COALESCE(is_email_sent, false)
    """))

    # Ensure the due date and name fields stay usable for future inserts.
    if _column_exists(bind, 'invoice', 'due_date'):
        op.execute(sa.text("UPDATE invoice SET due_date = COALESCE(due_date, COALESCE(issued_at::date, CURRENT_DATE) + 14)"))
    if _column_exists(bind, 'invoice', 'customer_name'):
        op.execute(sa.text("UPDATE invoice SET customer_name = COALESCE(NULLIF(customer_name, ''), 'Customer')"))
    if _column_exists(bind, 'invoice', 'customer_email'):
        op.execute(sa.text("UPDATE invoice SET customer_email = COALESCE(NULLIF(customer_email, ''), '')"))


def downgrade() -> None:
    bind = op.get_bind()
    if _table_exists(bind, 'invoice'):
        if _column_exists(bind, 'invoice', 'is_email_sent'):
            op.drop_column('invoice', 'is_email_sent')
        if _column_exists(bind, 'invoice', 'pdf_generated_at'):
            op.drop_column('invoice', 'pdf_generated_at')
        if _column_exists(bind, 'invoice', 'notes_snapshot'):
            op.drop_column('invoice', 'notes_snapshot')
        if _column_exists(bind, 'invoice', 'vehicle_snapshot'):
            op.drop_column('invoice', 'vehicle_snapshot')
        if _column_exists(bind, 'invoice', 'customer_email_snapshot'):
            op.drop_column('invoice', 'customer_email_snapshot')
        if _column_exists(bind, 'invoice', 'customer_name_snapshot'):
            op.drop_column('invoice', 'customer_name_snapshot')
        if _column_exists(bind, 'invoice', 'tax_rate'):
            op.drop_column('invoice', 'tax_rate')
