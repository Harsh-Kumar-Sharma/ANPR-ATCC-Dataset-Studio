"""Whether the database matches the code.

Every time this app gains a table, the database on disk is one
migration behind until someone runs alembic. From inside the app that
looked like a 500 and "An unexpected error occurred", which sends the
user looking for a bug that is not there.
"""

from dataclasses import asdict
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy.engine import make_url

from app.core.config import get_settings
from app.db.session import engine
from app.services import schema_state

router = APIRouter(prefix="/schema", tags=["schema"])


class SchemaStateRead(BaseModel):
    current: str | None
    head: str | None
    up_to_date: bool
    pending: list[str]


class SchemaUpgradeResult(BaseModel):
    state: SchemaStateRead
    #: Where the database was copied before it was changed.
    backup_path: str | None


def _database_path() -> Path | None:
    """The SQLite file behind the engine, if it is one."""
    url = make_url(get_settings().resolved_database_url())
    return Path(url.database) if url.get_backend_name() == "sqlite" and url.database else None


@router.get("", response_model=SchemaStateRead)
def get_schema_state() -> SchemaStateRead:
    """How far behind the database is, if at all."""
    return SchemaStateRead(**asdict(schema_state.state(engine)))


@router.post("/upgrade", response_model=SchemaUpgradeResult)
def upgrade_schema() -> SchemaUpgradeResult:
    """Bring the database up to date, after backing it up.

    The backup is not optional. This is the one routine operation in
    the app that rewrites real data, and an undo is worth a few
    megabytes.
    """
    state, saved = schema_state.upgrade(engine, _database_path())
    return SchemaUpgradeResult(
        state=SchemaStateRead(**asdict(state)),
        backup_path=str(saved) if saved else None,
    )
