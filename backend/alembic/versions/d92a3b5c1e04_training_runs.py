"""training runs

A model in the models directory is a file, and six weeks later nobody
remembers which labels went into it. A row here says which dataset
version, which model it started from and what settings were used.

Revision ID: d92a3b5c1e04
Revises: c81d4e0a7f36
Create Date: 2026-09-20 03:50:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d92a3b5c1e04"
down_revision: Union[str, None] = "c81d4e0a7f36"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "training_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("dataset_version_id", sa.String(length=36), nullable=False),
        sa.Column("base_model_id", sa.String(length=128), nullable=False),
        sa.Column("job_id", sa.String(length=36), nullable=True),
        sa.Column("epochs", sa.Integer(), nullable=False),
        sa.Column("image_size", sa.Integer(), nullable=False),
        sa.Column("settings_json", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("output_model_id", sa.String(length=128), nullable=True),
        sa.Column("best_map50", sa.Float(), nullable=True),
        sa.Column("last_epoch", sa.Integer(), nullable=True),
        sa.Column("error_message", sa.String(length=2048), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["dataset_version_id"], ["dataset_versions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_training_runs_project_id"), "training_runs", ["project_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_training_runs_project_id"), table_name="training_runs")
    op.drop_table("training_runs")
