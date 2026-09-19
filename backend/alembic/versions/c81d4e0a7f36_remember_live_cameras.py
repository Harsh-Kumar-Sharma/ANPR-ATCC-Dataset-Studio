"""remember live cameras

Starting a live capture meant retyping the URL, the frame rate, the
model and the keep-frames settings every time - including right after
a Stop, which is the most likely moment to want the same camera again.

Revision ID: c81d4e0a7f36
Revises: b3f1c72a58d9
Create Date: 2026-09-20 03:10:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c81d4e0a7f36"
down_revision: Union[str, None] = "b3f1c72a58d9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "live_cameras",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("rtsp_url", sa.String(length=1024), nullable=False),
        sa.Column("expected_fps", sa.Float(), nullable=False),
        sa.Column("model_id", sa.String(length=128), nullable=True),
        sa.Column("keep_frames", sa.Boolean(), nullable=False),
        sa.Column("keep_every", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
        # One row per camera per project: starting the same URL again
        # updates its settings rather than growing a list of copies.
        sa.UniqueConstraint("project_id", "rtsp_url", name="uq_live_camera_project_url"),
    )
    op.create_index(op.f("ix_live_cameras_project_id"), "live_cameras", ["project_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_live_cameras_project_id"), table_name="live_cameras")
    op.drop_table("live_cameras")
