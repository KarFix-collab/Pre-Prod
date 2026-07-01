"""Phase 2 workshop enhancement foundations.

Revision ID: 012
Revises: 011
Create Date: 2026-06-16
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "012"
down_revision: Union[str, None] = "011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

JOB_STATUSES = ('draft', 'in_progress', 'awaiting_parts', 'completed', 'delivered')


def _table_columns(bind, table_name):
    inspector = sa.inspect(bind)
    if table_name not in inspector.get_table_names():
        return set()
    return {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    bind = op.get_bind()
    job_columns = _table_columns(bind, 'job')
    history_columns = _table_columns(bind, 'job_status_history')
    attachment_columns = _table_columns(bind, 'job_attachment')

    if 'status' not in job_columns:
        op.add_column('job', sa.Column('status', sa.String(length=20), nullable=False, server_default='draft'))
        op.create_index(op.f('ix_job_status'), 'job', ['status'], unique=False)
    if 'internal_notes' not in job_columns:
        op.add_column('job', sa.Column('internal_notes', sa.Text(), nullable=True))
    if 'assigned_to' not in job_columns:
        op.add_column('job', sa.Column('assigned_to', sa.Integer(), nullable=True))
        op.create_foreign_key('fk_job_assigned_to_user', 'job', 'user', ['assigned_to'], ['user_id'], onupdate='CASCADE')

    if 'job_status_history' not in _table_columns(bind, 'job_status_history') and 'job_status_history' not in sa.inspect(bind).get_table_names():
        op.create_table(
            'job_status_history',
            sa.Column('history_id', sa.Integer(), primary_key=True),
            sa.Column('tenant_id', sa.Integer(), nullable=True),
            sa.Column('job_id', sa.Integer(), nullable=False),
            sa.Column('old_status', sa.String(length=20), nullable=True),
            sa.Column('new_status', sa.String(length=20), nullable=False),
            sa.Column('changed_by_user_id', sa.Integer(), nullable=True),
            sa.Column('note', sa.Text(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
            sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
            sa.ForeignKeyConstraint(['tenant_id'], ['tenant.tenant_id'], onupdate='CASCADE'),
            sa.ForeignKeyConstraint(['job_id'], ['job.job_id'], onupdate='CASCADE'),
            sa.ForeignKeyConstraint(['changed_by_user_id'], ['user.user_id'], onupdate='CASCADE'),
        )
        op.create_index(op.f('ix_job_status_history_tenant_id'), 'job_status_history', ['tenant_id'], unique=False)
        op.create_index(op.f('ix_job_status_history_job_id'), 'job_status_history', ['job_id'], unique=False)
        op.create_index(op.f('ix_job_status_history_new_status'), 'job_status_history', ['new_status'], unique=False)
        op.create_index(op.f('ix_job_status_history_changed_by_user_id'), 'job_status_history', ['changed_by_user_id'], unique=False)

    if 'job_attachment' not in sa.inspect(bind).get_table_names():
        op.create_table(
            'job_attachment',
            sa.Column('attachment_id', sa.Integer(), primary_key=True),
            sa.Column('tenant_id', sa.Integer(), nullable=True),
            sa.Column('job_id', sa.Integer(), nullable=False),
            sa.Column('filename', sa.String(length=255), nullable=False),
            sa.Column('file_url', sa.String(length=500), nullable=False),
            sa.Column('mime_type', sa.String(length=100), nullable=True),
            sa.Column('caption', sa.String(length=255), nullable=True),
            sa.Column('uploaded_by_user_id', sa.Integer(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
            sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
            sa.ForeignKeyConstraint(['tenant_id'], ['tenant.tenant_id'], onupdate='CASCADE'),
            sa.ForeignKeyConstraint(['job_id'], ['job.job_id'], onupdate='CASCADE'),
            sa.ForeignKeyConstraint(['uploaded_by_user_id'], ['user.user_id'], onupdate='CASCADE'),
        )
        op.create_index(op.f('ix_job_attachment_tenant_id'), 'job_attachment', ['tenant_id'], unique=False)
        op.create_index(op.f('ix_job_attachment_job_id'), 'job_attachment', ['job_id'], unique=False)
        op.create_index(op.f('ix_job_attachment_uploaded_by_user_id'), 'job_attachment', ['uploaded_by_user_id'], unique=False)

    # backfill existing jobs to sensible states
    if 'status' in job_columns:
        op.execute("UPDATE job SET status='completed' WHERE completed = 1 AND (status IS NULL OR status = '' OR status = 'draft')")
        op.execute("UPDATE job SET status='in_progress' WHERE completed = 0 AND (status IS NULL OR status = '' OR status = 'draft')")


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if 'job_attachment' in inspector.get_table_names():
        op.drop_index(op.f('ix_job_attachment_uploaded_by_user_id'), table_name='job_attachment')
        op.drop_index(op.f('ix_job_attachment_job_id'), table_name='job_attachment')
        op.drop_index(op.f('ix_job_attachment_tenant_id'), table_name='job_attachment')
        op.drop_table('job_attachment')
    if 'job_status_history' in inspector.get_table_names():
        op.drop_index(op.f('ix_job_status_history_changed_by_user_id'), table_name='job_status_history')
        op.drop_index(op.f('ix_job_status_history_new_status'), table_name='job_status_history')
        op.drop_index(op.f('ix_job_status_history_job_id'), table_name='job_status_history')
        op.drop_index(op.f('ix_job_status_history_tenant_id'), table_name='job_status_history')
        op.drop_table('job_status_history')
    job_columns = _table_columns(bind, 'job')
    if 'internal_notes' in job_columns:
        with op.batch_alter_table('job') as batch_op:
            batch_op.drop_column('internal_notes')
    if 'status' in job_columns:
        with op.batch_alter_table('job') as batch_op:
            batch_op.drop_index(op.f('ix_job_status'))
            batch_op.drop_column('status')
    if 'assigned_to' in job_columns:
        with op.batch_alter_table('job') as batch_op:
            batch_op.drop_constraint('fk_job_assigned_to_user', type_='foreignkey')
            batch_op.drop_column('assigned_to')
