"""Add supabase_user_id to user table for Supabase Auth linkage.

Revision ID: 009
Revises: 008
Create Date: 2026-06-14
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '009'
down_revision = '008'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('user', sa.Column('supabase_user_id', sa.String(length=255), nullable=True))
    op.create_unique_constraint('uq_user_supabase_user_id', 'user', ['supabase_user_id'])
    op.create_index('ix_user_supabase_user_id', 'user', ['supabase_user_id'], unique=False)


def downgrade():
    op.drop_index('ix_user_supabase_user_id', table_name='user')
    op.drop_constraint('uq_user_supabase_user_id', 'user', type_='unique')
    op.drop_column('user', 'supabase_user_id')
