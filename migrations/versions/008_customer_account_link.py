"""Link local users to customer portal profiles.

Revision ID: 008
Revises: 007
Create Date: 2026-06-12
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "008"
down_revision: Union[str, None] = "007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    user_table_exists = "user" in inspector.get_table_names()
    if not user_table_exists:
        return

    user_columns = {column["name"] for column in inspector.get_columns("user")}

    if "customer_id" not in user_columns:
        op.add_column("user", sa.Column("customer_id", sa.Integer(), nullable=True))
        op.create_index(op.f("ix_user_customer_id"), "user", ["customer_id"], unique=False)
        op.create_foreign_key(
            "fk_user_customer_id_customer",
            "user",
            "customer",
            ["customer_id"],
            ["customer_id"],
            onupdate="CASCADE",
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    user_table_exists = "user" in inspector.get_table_names()
    if not user_table_exists:
        return

    user_columns = {column["name"] for column in inspector.get_columns("user")}

    if "customer_id" in user_columns:
        with op.batch_alter_table("user") as batch_op:
            batch_op.drop_constraint("fk_user_customer_id_customer", type_="foreignkey")
            batch_op.drop_index(op.f("ix_user_customer_id"))
            batch_op.drop_column("customer_id")
