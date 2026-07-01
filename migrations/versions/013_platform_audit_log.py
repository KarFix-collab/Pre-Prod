"""Platform audit log for SuperAdmin actions.

Revision ID: 013
Revises: 012
Create Date: 2026-06-17
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '013'
down_revision: Union[str, None] = '012'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(bind, table_name: str) -> bool:
    inspector = sa.inspect(bind)
    return table_name in inspector.get_table_names()


def upgrade() -> None:
    bind = op.get_bind()
    if not _table_exists(bind, 'audit_log'):
        op.create_table(
            'audit_log',
            sa.Column('audit_log_id', sa.Integer(), primary_key=True),
            sa.Column('user_id', sa.Integer(), nullable=True),
            sa.Column('tenant_id', sa.Integer(), nullable=True),
            sa.Column('entity_type', sa.String(length=50), nullable=False),
            sa.Column('entity_id', sa.Integer(), nullable=True),
            sa.Column('action', sa.String(length=100), nullable=False),
            sa.Column('old_values', sa.JSON(), nullable=True),
            sa.Column('new_values', sa.JSON(), nullable=True),
            sa.Column('ip_address', sa.String(length=45), nullable=True),
            sa.Column('user_agent', sa.Text(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
            sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
            sa.ForeignKeyConstraint(['user_id'], ['user.user_id'], onupdate='CASCADE'),
            sa.ForeignKeyConstraint(['tenant_id'], ['tenant.tenant_id'], onupdate='CASCADE'),
        )
        op.create_index(op.f('ix_audit_log_user_id'), 'audit_log', ['user_id'], unique=False)
        op.create_index(op.f('ix_audit_log_tenant_id'), 'audit_log', ['tenant_id'], unique=False)
        op.create_index(op.f('ix_audit_log_entity_type'), 'audit_log', ['entity_type'], unique=False)
        op.create_index(op.f('ix_audit_log_entity_id'), 'audit_log', ['entity_id'], unique=False)
        op.create_index(op.f('ix_audit_log_action'), 'audit_log', ['action'], unique=False)
        op.create_index(op.f('ix_audit_log_created_at'), 'audit_log', ['created_at'], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    if _table_exists(bind, 'audit_log'):
        op.drop_index(op.f('ix_audit_log_created_at'), table_name='audit_log')
        op.drop_index(op.f('ix_audit_log_action'), table_name='audit_log')
        op.drop_index(op.f('ix_audit_log_entity_id'), table_name='audit_log')
        op.drop_index(op.f('ix_audit_log_entity_type'), table_name='audit_log')
        op.drop_index(op.f('ix_audit_log_tenant_id'), table_name='audit_log')
        op.drop_index(op.f('ix_audit_log_user_id'), table_name='audit_log')
        op.drop_table('audit_log')
