"""Invoice snapshots and frozen job pricing.

Revision ID: 014
Revises: 013
Create Date: 2026-06-18
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '014'
down_revision: Union[str, None] = '013'
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

    if _table_exists(bind, 'job_service'):
        if not _column_exists(bind, 'job_service', 'unit_cost'):
            op.add_column('job_service', sa.Column('unit_cost', sa.Numeric(10, 2), nullable=True))
        if not _column_exists(bind, 'job_service', 'line_total'):
            op.add_column('job_service', sa.Column('line_total', sa.Numeric(10, 2), nullable=True))

    if _table_exists(bind, 'job_part'):
        if not _column_exists(bind, 'job_part', 'unit_cost'):
            op.add_column('job_part', sa.Column('unit_cost', sa.Numeric(10, 2), nullable=True))
        if not _column_exists(bind, 'job_part', 'line_total'):
            op.add_column('job_part', sa.Column('line_total', sa.Numeric(10, 2), nullable=True))

    if not _table_exists(bind, 'invoice'):
        op.create_table(
            'invoice',
            sa.Column('invoice_id', sa.Integer(), primary_key=True),
            sa.Column('tenant_id', sa.Integer(), nullable=True),
            sa.Column('job_id', sa.Integer(), nullable=False),
            sa.Column('invoice_number', sa.String(length=40), nullable=False),
            sa.Column('customer_id', sa.Integer(), nullable=False),
            sa.Column('customer_name', sa.String(length=120), nullable=False),
            sa.Column('customer_email', sa.String(length=320), nullable=False),
            sa.Column('currency', sa.String(length=8), nullable=False, server_default='ZAR'),
            sa.Column('subtotal', sa.Numeric(12, 2), nullable=False, server_default='0'),
            sa.Column('tax_amount', sa.Numeric(12, 2), nullable=False, server_default='0'),
            sa.Column('total_amount', sa.Numeric(12, 2), nullable=False, server_default='0'),
            sa.Column('status', sa.String(length=20), nullable=False, server_default='draft'),
            sa.Column('issued_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
            sa.Column('due_date', sa.Date(), nullable=False),
            sa.Column('sent_at', sa.DateTime(), nullable=True),
            sa.Column('paid_at', sa.DateTime(), nullable=True),
            sa.Column('email_subject', sa.String(length=255), nullable=True),
            sa.Column('email_body', sa.Text(), nullable=True),
            sa.Column('line_items_json', sa.JSON(), nullable=False),
            sa.Column('delivery_status', sa.String(length=20), nullable=False, server_default='pending'),
            sa.Column('email_error', sa.Text(), nullable=True),
            sa.Column('is_email_enabled', sa.Boolean(), nullable=False, server_default=sa.text('false')),
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

    if _table_exists(bind, 'job_service'):
        op.execute(sa.text("""
            UPDATE job_service js
            SET unit_cost = COALESCE(js.unit_cost, s.cost),
                line_total = COALESCE(js.line_total, s.cost * js.qty)
            FROM service s
            WHERE js.service_id = s.service_id
        """))
    if _table_exists(bind, 'job_part'):
        op.execute(sa.text("""
            UPDATE job_part jp
            SET unit_cost = COALESCE(jp.unit_cost, p.cost),
                line_total = COALESCE(jp.line_total, p.cost * jp.qty)
            FROM part p
            WHERE jp.part_id = p.part_id
        """))
    if _table_exists(bind, 'job'):
        op.execute(sa.text("""
            UPDATE job j
            SET total_cost = COALESCE(s.sum_service_total, 0) + COALESCE(p.sum_part_total, 0)
            FROM (
                SELECT job_id, SUM(COALESCE(line_total, 0)) AS sum_service_total
                FROM job_service
                GROUP BY job_id
            ) s
            FULL OUTER JOIN (
                SELECT job_id, SUM(COALESCE(line_total, 0)) AS sum_part_total
                FROM job_part
                GROUP BY job_id
            ) p ON s.job_id = p.job_id
            WHERE j.job_id = COALESCE(s.job_id, p.job_id)
        """))


def downgrade() -> None:
    bind = op.get_bind()
    if _table_exists(bind, 'invoice'):
        op.drop_index(op.f('ix_invoice_status'), table_name='invoice')
        op.drop_index(op.f('ix_invoice_customer_id'), table_name='invoice')
        op.drop_index(op.f('ix_invoice_invoice_number'), table_name='invoice')
        op.drop_index(op.f('ix_invoice_job_id'), table_name='invoice')
        op.drop_index(op.f('ix_invoice_tenant_id'), table_name='invoice')
        op.drop_table('invoice')
    if _table_exists(bind, 'job_part') and _column_exists(bind, 'job_part', 'line_total'):
        op.drop_column('job_part', 'line_total')
    if _table_exists(bind, 'job_part') and _column_exists(bind, 'job_part', 'unit_cost'):
        op.drop_column('job_part', 'unit_cost')
    if _table_exists(bind, 'job_service') and _column_exists(bind, 'job_service', 'line_total'):
        op.drop_column('job_service', 'line_total')
    if _table_exists(bind, 'job_service') and _column_exists(bind, 'job_service', 'unit_cost'):
        op.drop_column('job_service', 'unit_cost')
