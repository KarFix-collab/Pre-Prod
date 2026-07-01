"""Add customer preferred tenant relationship.

Revision ID: 011
Revises: 010
Create Date: 2026-06-16
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "011"
down_revision: Union[str, None] = "010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    customer_columns = {column["name"] for column in inspector.get_columns("customer")} if "customer" in inspector.get_table_names() else set()

    if "preferred_tenant_id" not in customer_columns:
        op.add_column("customer", sa.Column("preferred_tenant_id", sa.Integer(), nullable=True))
        op.create_index(op.f("ix_customer_preferred_tenant_id"), "customer", ["preferred_tenant_id"], unique=False)
        op.create_foreign_key("fk_customer_preferred_tenant_id_tenant", "customer", "tenant", ["preferred_tenant_id"], ["tenant_id"], onupdate="CASCADE")


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    customer_columns = {column["name"] for column in inspector.get_columns("customer")} if "customer" in inspector.get_table_names() else set()

    if "preferred_tenant_id" in customer_columns:
        with op.batch_alter_table("customer") as batch_op:
            batch_op.drop_constraint("fk_customer_preferred_tenant_id_tenant", type_="foreignkey")
            batch_op.drop_index(op.f("ix_customer_preferred_tenant_id"))
            batch_op.drop_column("preferred_tenant_id")
