"""labeling tasks

A source's frames can be given to one person to label, and move from
assigned to done through an admin's review.

Revision ID: a6d2e8f41c07
Revises: f3b9a6c2d184
Create Date: 2026-10-03 08:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a6d2e8f41c07"
down_revision: Union[str, None] = "f3b9a6c2d184"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "labeling_tasks",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("source_id", sa.String(length=36), nullable=False),
        sa.Column("assignee_id", sa.String(length=36), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("review_note", sa.Text(), nullable=True),
        sa.Column("created_by_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("submitted_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["source_id"], ["sources.id"]),
        sa.ForeignKeyConstraint(["assignee_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_id"),
    )
    op.create_index(op.f("ix_labeling_tasks_project_id"), "labeling_tasks", ["project_id"])
    op.create_index(op.f("ix_labeling_tasks_assignee_id"), "labeling_tasks", ["assignee_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_labeling_tasks_assignee_id"), table_name="labeling_tasks")
    op.drop_index(op.f("ix_labeling_tasks_project_id"), table_name="labeling_tasks")
    op.drop_table("labeling_tasks")
