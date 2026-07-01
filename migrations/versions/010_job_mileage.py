"""Add mileage snapshot to jobs.

Revision ID: 010
Revises: 009
Create Date: 2026-06-15
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "010"
down_revision: Union[str, None] = "009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "job" in inspector.get_table_names():
        job_columns = {column["name"] for column in inspector.get_columns("job")}
        if "mileage" not in job_columns:
            op.add_column("job", sa.Column("mileage", sa.Integer(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "job" in inspector.get_table_names():
        job_columns = {column["name"] for column in inspector.get_columns("job")}
        if "mileage" in job_columns:
            with op.batch_alter_table("job") as batch_op:
                batch_op.drop_column("mileage")
