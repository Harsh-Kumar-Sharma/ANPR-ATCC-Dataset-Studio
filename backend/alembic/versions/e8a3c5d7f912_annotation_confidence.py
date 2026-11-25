"""annotation confidence

A dataset exported by a production node arrives with the model's own
boxes and how sure it was of each. The score is what puts the doubtful
boxes first in review, so the annotation keeps it.

Revision ID: e8a3c5d7f912
Revises: b4c1f7a9e2d3
Create Date: 2026-11-25 12:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e8a3c5d7f912"
down_revision: Union[str, None] = "b4c1f7a9e2d3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_annotations() -> bool:
    # Every real database has the table; a bare one built only to test
    # the upgrade path does not, and adding a column to nothing fails.
    return "annotations" in sa.inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    if not _has_annotations():
        return
    with op.batch_alter_table("annotations") as batch:
        batch.add_column(sa.Column("confidence", sa.Float(), nullable=True))


def downgrade() -> None:
    if not _has_annotations():
        return
    with op.batch_alter_table("annotations") as batch:
        batch.drop_column("confidence")
