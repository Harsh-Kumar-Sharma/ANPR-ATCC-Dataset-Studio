"""What each job type actually does.

A handler receives its job's params and a ``report`` callable, and
returns whatever the UI needs to link to the result. It runs inside the
worker process, so it must build everything it needs from params alone -
it shares no memory with the app that submitted it.
"""

import logging
from pathlib import Path

from sqlalchemy.orm import Session

from app.db.models.processing_run import ProcessingRun
from app.db.models.project import Project
from app.db.models.source import Source
from app.ml.factory import create_tracker, get_default_detector
from app.services.track_processor import ProgressReporter, process_source

logger = logging.getLogger(__name__)


def run_detect_job(db: Session, params: dict, report: ProgressReporter) -> dict:
    """Detect and track vehicles across a source video.

    The processing run row is created by the endpoint that submitted the
    job, not here, so the caller can link to it immediately rather than
    waiting for a worker to start.
    """
    run_id = params["run_id"]
    run = db.get(ProcessingRun, run_id)
    if run is None:
        raise LookupError(f"No such processing run: {run_id}")

    source = db.get(Source, run.source_id)
    if source is None:
        raise LookupError(f"No such source: {run.source_id}")

    project = db.get(Project, source.project_id)
    if project is None:
        raise LookupError(f"No such project: {source.project_id}")

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
        db.commit()
        raise

    return {"run_id": run.id, "source_id": source.id, "project_id": project.id}


HANDLERS = {
    "detect": run_detect_job,
}
