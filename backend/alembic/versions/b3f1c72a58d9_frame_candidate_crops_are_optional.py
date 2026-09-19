"""frame candidate crops are optional

A crop is written only when its pixels cannot be recovered - a live
capture. An offline run cuts the crop out of the source video on
demand instead of writing one JPEG per vehicle per frame, which a
full-rate pass over a long video cannot afford.

Revision ID: b3f1c72a58d9
Revises: 7c41d0a9e5b2
Create Date: 2026-09-20 01:40:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b3f1c72a58d9"
down_revision: Union[str, None] = "7c41d0a9e5b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("frame_candidates") as batch:
        batch.alter_column("image_path", existing_type=sa.String(length=1024), nullable=True)


def downgrade() -> None:
    """Rows written since the upgrade have no crop file to point at.

    Going back means inventing a path for them, which would be a lie
    the reviewer would meet as a broken image. The empty string is at
    least honestly empty, and the column is only narrowed back to
    NOT NULL after they are filled.
    """
    op.execute("UPDATE frame_candidates SET image_path = '' WHERE image_path IS NULL")
    with op.batch_alter_table("frame_candidates") as batch:
        batch.alter_column("image_path", existing_type=sa.String(length=1024), nullable=False)
