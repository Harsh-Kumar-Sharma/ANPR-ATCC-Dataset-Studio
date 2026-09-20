"""Live sources that caught nothing, cleared away.

Every live capture start creates a source. A session that detected
nothing and kept no frames leaves that source behind holding
absolutely nothing - and after a few attempts at getting a camera
working, the Sources list is five identical rows reading "0x0 @
10fps · 0 frames" with the one that matters somewhere among them.

Only live sources. A video that was just imported also has no frames
yet, and deleting it out from under someone who is about to press
Detect would be a far worse bug than the one this fixes.
"""

import logging
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models.frame import Frame
from app.db.models.processing_run import ProcessingRun
from app.db.models.project import Project
from app.db.models.source import Source
from app.db.models.track import Track
from app.services import source_deletion
from app.services.live_reconcile import UNFINISHED

logger = logging.getLogger(__name__)


def _holds_nothing(db: Session, source: Source) -> bool:
    """Nothing was captured, detected or kept from this source.

    Tracks are checked as well as frames. They should not exist
    without frames - every observation creates one - but a source is
    only safe to remove if *nothing* points at it, and relying on an
    invariant to skip a count is how the exception gets deleted.
    """
    frames = db.scalar(select(func.count(Frame.id)).where(Frame.source_id == source.id)) or 0
    if frames:
        return False

    runs = list(db.scalars(select(ProcessingRun.id).where(ProcessingRun.source_id == source.id)))
    if not runs:
        # No run at all: nothing has happened to this source yet, and
        # something else may be about to. Not ours to remove.
        return False

    tracks = db.scalar(select(func.count(Track.id)).where(Track.run_id.in_(runs))) or 0
    return tracks == 0


def _is_busy(db: Session, source: Source) -> bool:
    """A capture still going, or one whose row says so."""
    return (
        db.scalar(
            select(func.count(ProcessingRun.id)).where(
                ProcessingRun.source_id == source.id, ProcessingRun.status.in_(UNFINISHED)
            )
        )
        or 0
    ) > 0


def find_empty(db: Session, project_id: str | None = None) -> list[Source]:
    """Live sources safe to remove: finished, and holding nothing."""
    stmt = select(Source).where(Source.type == "rtsp")
    if project_id is not None:
        stmt = stmt.where(Source.project_id == project_id)
    return [
        source
        for source in db.scalars(stmt)
        if not _is_busy(db, source) and _holds_nothing(db, source)
    ]


def remove_empty(db: Session, project_id: str | None = None) -> list[str]:
    """Delete them, through the same cascade a manual removal uses.

    The caller commits; files are removed after, the order every
    deletion in this app settled on.
    """
    removed: list[str] = []
    to_remove = find_empty(db, project_id)
    if not to_remove:
        return removed

    owned = []
    for source in to_remove:
        project = db.get(Project, source.project_id)
        if project is None:
            continue
        _, files = source_deletion.delete_source(db, source, Path(project.workspace_path))
        removed.append(source.id)
        owned.append(files)

    db.commit()
    for files in owned:
        source_deletion.remove_source_files(files)

    logger.info("Removed %d live source(s) that captured nothing: %s", len(removed), ", ".join(removed))
    return removed
