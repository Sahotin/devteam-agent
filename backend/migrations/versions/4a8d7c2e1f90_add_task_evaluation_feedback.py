"""add task evaluation feedback

Revision ID: 4a8d7c2e1f90
Revises: 7d2f6b1a9c04
Create Date: 2026-08-11 02:00:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "4a8d7c2e1f90"
down_revision: Union[str, Sequence[str], None] = "7d2f6b1a9c04"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "task_evaluation_feedback" in inspector.get_table_names():
        expected = {
            "task_id", "rating", "accepted", "comment", "created_at", "updated_at"
        }
        actual = {column["name"] for column in inspector.get_columns("task_evaluation_feedback")}
        missing = expected.difference(actual)
        if missing:
            raise RuntimeError(
                "任务评价表结构不完整，缺少字段：" + ", ".join(sorted(missing))
            )
        return
    op.create_table(
        "task_evaluation_feedback",
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("rating", sa.Integer(), nullable=False),
        sa.Column("accepted", sa.Boolean(), nullable=False),
        sa.Column("comment", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("task_id"),
    )


def downgrade() -> None:
    if "task_evaluation_feedback" in sa.inspect(op.get_bind()).get_table_names():
        op.drop_table("task_evaluation_feedback")
