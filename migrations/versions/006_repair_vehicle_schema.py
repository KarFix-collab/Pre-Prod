"""Repair vehicle schema for existing deployments.

Revision ID: 006
Revises: 005
Create Date: 2026-06-12
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "006"
down_revision: Union[str, None] = "005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "vehicle" not in inspector.get_table_names():
        op.create_table(
            "vehicle",
            sa.Column("vehicle_id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenant.tenant_id"), nullable=True),
            sa.Column(
                "customer_id",
                sa.Integer(),
                sa.ForeignKey("customer.customer_id", onupdate="CASCADE"),
                nullable=False,
            ),
            sa.Column("make", sa.String(length=50), nullable=False),
            sa.Column("model", sa.String(length=50), nullable=False),
            sa.Column("year", sa.Integer(), nullable=True),
            sa.Column("registration_number", sa.String(length=30), nullable=True),
            sa.Column("vin", sa.String(length=50), nullable=True),
            sa.Column("color", sa.String(length=30), nullable=True),
            sa.Column("mileage", sa.Integer(), nullable=True),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("is_primary", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )
        op.create_index(op.f("ix_vehicle_tenant_id"), "vehicle", ["tenant_id"], unique=False)
        op.create_index(op.f("ix_vehicle_customer_id"), "vehicle", ["customer_id"], unique=False)

    if "job" in inspector.get_table_names():
        job_columns = {column["name"] for column in inspector.get_columns("job")}
        if "vehicle_id" not in job_columns:
            op.add_column("job", sa.Column("vehicle_id", sa.Integer(), nullable=True))
            op.create_index(op.f("ix_job_vehicle_id"), "job", ["vehicle_id"], unique=False)
            op.create_foreign_key(
                "fk_job_vehicle_id_vehicle",
                "job",
                "vehicle",
                ["vehicle_id"],
                ["vehicle_id"],
                onupdate="CASCADE",
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "job" in inspector.get_table_names():
        job_columns = {column["name"] for column in inspector.get_columns("job")}
        if "vehicle_id" in job_columns:
            with op.batch_alter_table("job") as batch_op:
                batch_op.drop_constraint("fk_job_vehicle_id_vehicle", type_="foreignkey")
                batch_op.drop_index(op.f("ix_job_vehicle_id"))
                batch_op.drop_column("vehicle_id")

    if "vehicle" in inspector.get_table_names():
        with op.batch_alter_table("vehicle") as batch_op:
            batch_op.drop_index(op.f("ix_vehicle_customer_id"))
            batch_op.drop_index(op.f("ix_vehicle_tenant_id"))
        op.drop_table("vehicle")
