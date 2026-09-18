"""move annotations onto frames

Revision ID: 2346228ab587
Revises: e76306d9efc0
Create Date: 2026-09-19 02:31:04.318221

The frame becomes the unit of labelling. An annotation hangs off a
frame directly, many per frame; the frame-candidate link survives only
as a record of which detection a legacy track-review label came from.

SQLite cannot add a NOT NULL column to a populated table, alter a
column's nullability, or add a foreign key in place. Everything here
goes through batch mode, which recreates the table, and the backfill
sits between two batch passes: add frame_id nullable, fill it from the
candidate, then make it required.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2346228ab587'
down_revision: Union[str, None] = 'e76306d9efc0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Frames first: the new annotation foreign key points at them.
    with op.batch_alter_table("frames") as batch:
        batch.add_column(sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"))
        batch.add_column(sa.Column("selection_reason", sa.String(length=64), nullable=True))
        batch.create_index("ix_frames_status", ["status"])

    # Pass 1: the new columns, nullable so the existing rows survive the
    # table recreate, and the candidate link relaxed to optional.
    with op.batch_alter_table("annotations") as batch:
        batch.add_column(sa.Column("frame_id", sa.String(length=36), nullable=True))
        batch.add_column(sa.Column("attributes", sa.JSON(), nullable=False, server_default="{}"))
        batch.alter_column("frame_candidate_id", existing_type=sa.VARCHAR(length=36), nullable=True)

    # Backfill: every existing label was written through track review,
    # so its candidate knows which full frame it came from.
    connection = op.get_bind()
    connection.execute(
        sa.text(
            "UPDATE annotations SET frame_id = ("
            "  SELECT fc.frame_id FROM frame_candidates fc WHERE fc.id = annotations.frame_candidate_id"
            ")"
        )
    )

    # A candidate from before full frames were captured has no frame to
    # hang its label on. The replan's answer is to re-run detection
    # rather than migrate such rows, and judges the handful of existing
    # labels not worth a migration path - so they go, and the count is
    # printed so nobody discovers it by surprise.
    orphaned = connection.execute(sa.text("SELECT COUNT(*) FROM annotations WHERE frame_id IS NULL")).scalar() or 0
    if orphaned:
        print(f"[2346228ab587] dropping {orphaned} annotation(s) whose candidate predates full-frame capture")
        connection.execute(sa.text("DELETE FROM annotations WHERE frame_id IS NULL"))

    # Pass 2: now that every row has a frame, make it required and wire
    # the foreign key and index.
    with op.batch_alter_table("annotations") as batch:
        batch.alter_column("frame_id", existing_type=sa.String(length=36), nullable=False)
        batch.create_foreign_key("fk_annotations_frame_id_frames", "frames", ["frame_id"], ["id"])
        batch.create_index("ix_annotations_frame_id", ["frame_id"])


def downgrade() -> None:
    # A box drawn on the canvas has no candidate. The old model cannot
    # represent it, so it cannot survive a downgrade.
    connection = op.get_bind()
    canvas_only = (
        connection.execute(sa.text("SELECT COUNT(*) FROM annotations WHERE frame_candidate_id IS NULL")).scalar() or 0
    )
    if canvas_only:
        print(f"[2346228ab587] dropping {canvas_only} canvas annotation(s) the pre-frame model cannot hold")
        connection.execute(sa.text("DELETE FROM annotations WHERE frame_candidate_id IS NULL"))

    with op.batch_alter_table("annotations") as batch:
        batch.drop_index("ix_annotations_frame_id")
        batch.drop_constraint("fk_annotations_frame_id_frames", type_="foreignkey")
        batch.alter_column("frame_candidate_id", existing_type=sa.VARCHAR(length=36), nullable=False)
        batch.drop_column("attributes")
        batch.drop_column("frame_id")

    with op.batch_alter_table("frames") as batch:
        batch.drop_index("ix_frames_status")
        batch.drop_column("selection_reason")
        batch.drop_column("status")
