"""source names

Live sources from one camera all read "host · ch 1". An admin can now
give a source a name of its own.

Revision ID: b4c1f7a9e2d3
Revises: a6d2e8f41c07
Create Date: 2026-10-03 09:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b4c1f7a9e2d3"
down_revision: Union[str, None] = "a6d2e8f41c07"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_sources() -> bool:
    # Every real database has the table; a bare one built only to test
    # the upgrade path does not, and adding a column to nothing fails.
    return "sources" in sa.inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    if not _has_sources():
        return
    with op.batch_alter_table("sources") as batch:
        batch.add_column(sa.Column("name", sa.String(length=128), nullable=True))


def downgrade() -> None:
    if not _has_sources():
        return
    with op.batch_alter_table("sources") as batch:
        batch.drop_column("name")
