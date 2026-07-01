"""Customer portal preferences and workshop selection.

Revision ID: 007
Revises: 006
Create Date: 2026-06-12
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "007"
down_revision: Union[str, None] = "006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    user_columns = {column["name"] for column in inspector.get_columns("user")} if "user" in inspector.get_table_names() else set()

    if "preferred_tenant_id" not in user_columns:
        op.add_column("user", sa.Column("preferred_tenant_id", sa.Integer(), nullable=True))
        op.create_index(op.f("ix_user_preferred_tenant_id"), "user", ["preferred_tenant_id"], unique=False)
        op.create_foreign_key("fk_user_preferred_tenant_id_tenant", "user", "tenant", ["preferred_tenant_id"], ["tenant_id"], onupdate="CASCADE")


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    user_columns = {column["name"] for column in inspector.get_columns("user")} if "user" in inspector.get_table_names() else set()

    if "preferred_tenant_id" in user_columns:
        with op.batch_alter_table("user") as batch_op:
            batch_op.drop_constraint("fk_user_preferred_tenant_id_tenant", type_="foreignkey")
            batch_op.drop_index(op.f("ix_user_preferred_tenant_id"))
            batch_op.drop_column("preferred_tenant_id")
