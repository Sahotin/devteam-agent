"""add task policy decisions

Revision ID: 7d2f6b1a9c04
Revises: 1e5ea4895c29
Create Date: 2026-08-11 00:00:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "7d2f6b1a9c04"
down_revision: Union[str, Sequence[str], None] = "1e5ea4895c29"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if "task_policies" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "task_policies",
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("execution_scope", sa.String(length=30), nullable=False),
        sa.Column("preference", sa.String(length=30), nullable=False),
        sa.Column("risk_score", sa.Integer(), nullable=False),
        sa.Column("governance_level", sa.String(length=30), nullable=False),
        sa.Column("reasons", sa.JSON(), nullable=False),
        sa.Column("hard_risk_flags", sa.JSON(), nullable=False),
        sa.Column("assessed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["tasks.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("task_id"),
    )


def downgrade() -> None:
    if "task_policies" in sa.inspect(op.get_bind()).get_table_names():
        op.drop_table("task_policies")
