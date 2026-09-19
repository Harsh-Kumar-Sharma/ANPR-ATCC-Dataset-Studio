"""Live captures do not survive a restart, and the rows have to say so.

A capture session is threads and sockets inside the backend process.
When that process goes, the session goes with it - but the
processing_run row stayed "running" forever. Nothing could stop it
either: the stop endpoint looks the session up in an in-memory
registry that restarted empty.

The visible damage was a source that could not be deleted. The guard
counts unfinished runs against a source, so two abandoned live runs
blocked it permanently, with a message telling the user to stop a
capture that no longer existed.
"""

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.processing_run import ProcessingRun
from app.services.rtsp_session_registry import get_session

logger = logging.getLogger(__name__)

#: Statuses that mean "still going", and so still block the source.
UNFINISHED = ("pending", "running")

ABANDONED_MESSAGE = (
    "The app closed while this live capture was running, so it stopped there. "
    "Whatever it had already captured is kept."
)


def is_live(run: ProcessingRun) -> bool:
    """A run from the live capture path rather than an offline pass."""
    config = run.sampling_config or {}
    return config.get("protocol") == "rtsp"


def unfinished_live_runs(db: Session) -> list[ProcessingRun]:
    return [
        run
        for run in db.scalars(select(ProcessingRun).where(ProcessingRun.status.in_(UNFINISHED)))
        if is_live(run)
    ]


def settle(db: Session, run: ProcessingRun, message: str = ABANDONED_MESSAGE) -> None:
    """Mark one abandoned live run as over.

    "failed" rather than "cancelled": nobody chose to end it. The
    message says what happened, because "failed" on its own invites
    the reader to go looking for a fault in the camera.
    """
    run.status = "failed"
    run.error_message = message
    run.completed_at = datetime.now(timezone.utc)


def reconcile_live_captures(db: Session) -> list[str]:
    """Settle every live run that no session is backing.

    Called at startup, where the answer is always "none of them": the
    registry is empty in a process that has just begun. It still asks
    rather than assuming, so the same function is safe to call later -
    and so a running capture is never settled out from under itself.
    """
    abandoned = [run for run in unfinished_live_runs(db) if get_session(run.id) is None]
    for run in abandoned:
        settle(db, run)
    if abandoned:
        db.commit()
        logger.warning(
            "Settled %d live capture(s) left running by a previous session: %s",
            len(abandoned),
            ", ".join(run.id for run in abandoned),
        )
    return [run.id for run in abandoned]
