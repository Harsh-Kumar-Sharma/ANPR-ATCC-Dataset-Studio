"""Whether the database matches the code, and bringing it up to date.

Every time this app gains a table, the database on disk is one
migration behind until someone runs alembic. What that looks like
from inside the app is a 500 and "An unexpected error occurred",
which tells the user nothing and sends them looking for a bug that
is not there.

So: say it plainly, and offer to do it - after taking a backup,
because a migration is the one routine operation here that rewrites
real data.
"""

import logging
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine

from app.core.errors import AppError

logger = logging.getLogger(__name__)


class SchemaUpgradeError(AppError):
    code = "schema_upgrade_failed"


@dataclass
class SchemaState:
    """Where the database is against where the code expects it."""

    current: str | None
    head: str | None
    up_to_date: bool
    #: Every revision between the two, so the user can see how far
    #: behind they are rather than just that they are.
    pending: list[str]


def _alembic_config(backend_root: Path, target_url: str | None = None) -> Config:
    config = Config(str(backend_root / "alembic.ini"))
    config.set_main_option("script_location", str(backend_root / "alembic"))
    if target_url is not None:
        # Named explicitly rather than left to the settings: this
        # function is handed an engine, and migrating a different
        # database than the one it was given is the kind of bug that
        # is only noticed afterwards.
        config.attributes["target_url"] = target_url
    return config


def backend_root() -> Path:
    """The directory holding alembic.ini - two up from this file."""
    return Path(__file__).resolve().parents[2]


def state(engine: Engine, root: Path | None = None) -> SchemaState:
    """Compare the database's revision with the code's."""
    root = root or backend_root()
    script = ScriptDirectory.from_config(_alembic_config(root))
    head = script.get_current_head()

    with engine.connect() as connection:
        current = MigrationContext.configure(connection).get_current_revision()

    if current == head:
        return SchemaState(current=current, head=head, up_to_date=True, pending=[])

    # Walked from head backwards to the current revision. A database
    # at an unknown revision - a newer app ran against it once - is
    # reported as behind with no list rather than crashing here.
    pending: list[str] = []
    try:
        for revision in script.iterate_revisions(head, current):
            pending.append(revision.revision)
    except Exception:  # noqa: BLE001 - a broken chain must not break the check
        logger.warning("Could not list pending revisions from %s to %s", current, head, exc_info=True)

    return SchemaState(current=current, head=head, up_to_date=False, pending=list(reversed(pending)))


def backup(database_path: Path) -> Path:
    """Copy the database somewhere safe before changing it.

    Through SQLite's own backup API rather than copying the file:
    in WAL mode the .db file is only the last checkpoint, and a plain
    copy silently loses whatever is in the write-ahead log. That has
    already cost this project data once.
    """
    destination = database_path.parent / "backups" / (
        f"{database_path.stem}-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}.db"
    )
    destination.parent.mkdir(parents=True, exist_ok=True)

    source = sqlite3.connect(f"file:{database_path.as_posix()}?mode=ro", uri=True)
    try:
        target = sqlite3.connect(destination)
        try:
            source.backup(target)
        finally:
            target.close()
    finally:
        source.close()
    return destination


def upgrade(engine: Engine, database_path: Path | None, root: Path | None = None) -> tuple[SchemaState, Path | None]:
    """Bring the database up to the code's revision, backing it up first.

    Returns the state afterwards and where the backup went. The
    backup is not optional: this is the one routine operation in the
    app that rewrites real data, and an undo is worth a few megabytes.
    """
    root = root or backend_root()

    saved: Path | None = None
    if database_path is not None and database_path.is_file():
        try:
            saved = backup(database_path)
            logger.info("Backed up the database to %s before upgrading", saved)
        except (OSError, sqlite3.Error) as exc:
            raise SchemaUpgradeError(
                f"Could not back up the database before upgrading it: {exc}. "
                "Nothing was changed."
            ) from exc

    try:
        command.upgrade(_alembic_config(root, str(engine.url)), "head")
    except Exception as exc:  # noqa: BLE001 - reported, not swallowed
        raise SchemaUpgradeError(
            f"The upgrade failed: {exc}. "
            + (f"Your database is unchanged in {saved}." if saved else "")
        ) from exc

    return state(engine, root), saved
