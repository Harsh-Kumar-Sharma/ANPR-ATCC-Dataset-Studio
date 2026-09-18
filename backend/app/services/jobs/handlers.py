"""What each job type actually does.

A handler receives its job's params and a ``report`` callable, and
returns whatever the UI needs to link to the result. It runs inside the
worker process, so it must build everything it needs from params alone -
it shares no memory with the app that submitted it.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
from app.db.models.processing_run import ProcessingRun
from app.db.models.project import Project
from app.db.models.source import Source
from app.ml.factory import create_tracker, get_default_detector
from app.services.track_processor import ProgressReporter, process_source

logger = logging.getLogger(__name__)

#: A run that already settled must not be relabelled by late cleanup.
TERMINAL_RUN_STATUSES = frozenset({"completed", "failed", "cancelled"})


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def run_detect_job(db: Session, params: dict, report: ProgressReporter) -> dict:
    """Detect and track vehicles across a source video.

    The processing run row is created by the endpoint that submitted the
    job, not here, so the caller can link to it immediately rather than
    waiting for a worker to start.
    """
    run_id = params["run_id"]
    run = db.get(ProcessingRun, run_id)
    if run is None:
        raise NotFoundError(f"Processing run not found: {run_id}")

    source = db.get(Source, run.source_id)
    if source is None:
        raise NotFoundError(f"Source not found: {run.source_id}")

    project = db.get(Project, source.project_id)
    if project is None:
        raise NotFoundError(f"Project not found: {source.project_id}")

    target_fps = float(run.sampling_config["target_fps"])

    report(0.0, "Loading detector")
    detector = get_default_detector()
    tracker = create_tracker(frame_rate=target_fps)

    # Recorded here rather than at submission: the worker is what
    # actually loads a model, so it is the only thing that can honestly
    # say which one ran.
    run.detector_version = detector.model_version
    run.status = "running"
    db.commit()

    try:
        process_source(
            db=db,
            source=source,
            workspace_path=Path(project.workspace_path),
            run=run,
            target_fps=target_fps,
            detector=detector,
            tracker=tracker,
            on_progress=report,
        )
    except BaseException as exc:
        # The run and the job both have to reflect the failure: the job
        # is how the user sees it, the run is what the rest of the app
        # reads. Leaving the run "running" forever would block the
        # source against any future run.
        run.status = "failed"
        run.error_message = str(exc)[:2048]
        run.completed_at = _utcnow()
        db.commit()
        raise

    # process_source marks the run completed but has no opinion on when,
    # because it is also used by paths that are not jobs. Stamping it
    # here keeps "when did this run stop" true for both outcomes - a run
    # with a null completed_at reads as still going, forever.
    run.completed_at = _utcnow()
    db.commit()

    return {"run_id": run.id, "source_id": source.id, "project_id": project.id}




def settle_detect_job(db: Session, params: dict, outcome: str) -> None:
    """Leave an abandoned detect run in an honest state.

    Without this the run stays "running" forever, which both misreports
    what happened and blocks the source against any future run. Partial
    tracks already written are kept - they are real observations - but
    the run they belong to never claims to have completed.

    ``outcome`` mirrors the job's own terminal status, so the two never
    tell the user different stories.
    """
    run_id = params.get("run_id")
    if run_id is None:
        return
    run = db.get(ProcessingRun, run_id)
    if run is None or run.status in TERMINAL_RUN_STATUSES:
        return
    run.status = outcome
    run.completed_at = _utcnow()
    db.commit()


@dataclass(frozen=True)
class JobHandler:
    """Everything the job system needs to know about one job type.

    One registry rather than a parallel map per lifecycle event, so
    adding a job type is a single edit in one place.
    """

    run: Callable[[Session, dict, ProgressReporter], dict]
    #: Called when a job ends without its handler finishing - cancelled,
    #: or reconciled after its process died. Receives the job's terminal
    #: status so anything it owns can be left saying the same thing.
    on_abort: Callable[[Session, dict, str], None] | None = None


HANDLERS: dict[str, JobHandler] = {
    "detect": JobHandler(run=run_detect_job, on_abort=settle_detect_job),
}
