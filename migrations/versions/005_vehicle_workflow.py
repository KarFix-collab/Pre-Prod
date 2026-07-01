"""Add vehicle support for workshop tenants

Revision ID: 005
Revises: 004
Create Date: 2026-06-12
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '005'
down_revision: Union[str, None] = '004'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'vehicle',
        sa.Column('vehicle_id', sa.Integer(), primary_key=True, nullable=False),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenant.tenant_id'), nullable=True),
        sa.Column('customer_id', sa.Integer(), sa.ForeignKey('customer.customer_id', onupdate='CASCADE'), nullable=False),
        sa.Column('make', sa.String(length=50), nullable=False),
        sa.Column('model', sa.String(length=50), nullable=False),
        sa.Column('year', sa.Integer(), nullable=True),
        sa.Column('registration_number', sa.String(length=30), nullable=True),
        sa.Column('vin', sa.String(length=50), nullable=True),
        sa.Column('color', sa.String(length=30), nullable=True),
        sa.Column('mileage', sa.Integer(), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('is_primary', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
    )
    op.create_index(op.f('ix_vehicle_tenant_id'), 'vehicle', ['tenant_id'], unique=False)
    op.create_index(op.f('ix_vehicle_customer_id'), 'vehicle', ['customer_id'], unique=False)

    op.add_column('job', sa.Column('vehicle_id', sa.Integer(), nullable=True))
    op.create_index(op.f('ix_job_vehicle_id'), 'job', ['vehicle_id'], unique=False)
    op.create_foreign_key('fk_job_vehicle_id_vehicle', 'job', 'vehicle', ['vehicle_id'], ['vehicle_id'], onupdate='CASCADE')


def downgrade() -> None:
    op.drop_constraint('fk_job_vehicle_id_vehicle', 'job', type_='foreignkey')
    op.drop_index(op.f('ix_job_vehicle_id'), table_name='job')
    op.drop_column('job', 'vehicle_id')

    op.drop_index(op.f('ix_vehicle_customer_id'), table_name='vehicle')
    op.drop_index(op.f('ix_vehicle_tenant_id'), table_name='vehicle')
    op.drop_table('vehicle')
