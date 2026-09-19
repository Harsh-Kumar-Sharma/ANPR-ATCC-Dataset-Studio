"""Deleting rows that own other rows, safely and in bulk.

Shared by the project and source deletions, which are the same shape at
two scales. Both have to sweep by hand because nothing here enforces
the foreign keys - SQLite is not asked to - so the database will
happily keep rows pointing at a parent that is gone. Orphans do not
announce themselves; they sit there being counted by the next query
that forgets to scope itself.

Both also have to work at real sizes. A single source can hold tens of
thousands of frames, and one bound parameter per frame runs into
SQLite's 32 766 cap - not only when deleting them but when *gathering*
the ids of what hangs off them, which is the version of this bug that
made a large project undeletable with a generic 500.
"""

from pathlib import Path
from shutil import rmtree

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.services.annotations import chunked


def ids_in(db: Session, id_column, match_column, values: list[str]) -> list[str]:
    """Ids whose ``match_column`` is one of ``values``, gathered in chunks."""
    if not values:
        return []
    found: list[str] = []
    for chunk in chunked(values):
        found.extend(db.scalars(select(id_column).where(match_column.in_(chunk))))
    return found


def delete_in(db: Session, model, column, ids: list[str]) -> None:
    """Delete rows whose ``column`` is one of ``ids``, in chunks."""
    for chunk in chunked(ids):
        db.execute(delete(model).where(column.in_(chunk)))


def directory_size(path: Path | None) -> int:
    """Bytes under a directory, or 0 if there is not one."""
    if path is None or not path.is_dir():
        return 0
    total = 0
    for entry in path.rglob("*"):
        try:
            if entry.is_file():
                total += entry.stat().st_size
        except OSError:
            # A file that vanished while being counted is a file that is
            # not going to be there to delete either.
            continue
    return total


def file_size(path: Path | None) -> int:
    if path is None:
        return 0
    try:
        return path.stat().st_size if path.is_file() else 0
    except OSError:
        return 0


def remove_tree(path: Path) -> bool:
    """Delete a directory and say whether it actually went.

    ``ignore_errors`` keeps a file held open by the desktop app from
    raising, but then the only signal is the return value - a caller
    that reports "removed" without checking would be lying.
    """
    if not path.is_dir():
        return False
    rmtree(path, ignore_errors=True)
    return not path.exists()


def remove_file(path: Path) -> bool:
    try:
        path.unlink()
        return True
    except (OSError, FileNotFoundError):
        return False


def is_inside(path: Path, root: Path) -> bool:
    """Is ``path`` somewhere under ``root``?

    The guard in front of every recursive delete here. A path that comes
    out of a database column is a path whatever wrote that column chose,
    and the blast radius of getting it wrong is everything underneath.
    """
    try:
        resolved = path.resolve()
        base = root.resolve()
    except OSError:
        return False
    return resolved != base and base in resolved.parents
