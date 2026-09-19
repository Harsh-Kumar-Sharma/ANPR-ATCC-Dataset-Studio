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

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
from app.db.models.frame import Frame
from app.db.models.frame_candidate import FrameCandidate
from app.db.models.dataset_version import DatasetVersion
from app.db.models.processing_run import ProcessingRun
from app.db.models.project import Project
from app.db.models.source import Source
from app.db.models.training_run import TrainingRun
from app.core.config import get_settings
from app.ml.factory import create_tracker, get_detector
from app.ml.models import DEFAULT_MODEL_ID
from app.ml.weights import ensure_weights
from app.services import training
from app.services.training import train_with_ultralytics as train
from app.services.frame_sampler import SampledFrame, decode_sampled_frames
from app.services.frame_selection import FrameSignals, brightness_of, frame_quality, perceptual_hash, select_frames
from app.services.track_processor import ProgressReporter, process_source

logger = logging.getLogger(__name__)

#: A run that already settled must not be relabelled by late cleanup.
TERMINAL_RUN_STATUSES = frozenset({"completed", "failed", "cancelled"})

#: Decoding dominates a selection run, so it owns most of the bar.
READING_SHARE = 0.9
PROGRESS_EVERY = 10


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
    every_frame = bool(run.sampling_config.get("every_frame", False))

    # The model the request asked for, or the default for a request
    # written before there was a choice.
    model_id = params.get("model_id") or run.detector_version or DEFAULT_MODEL_ID

    # Fetched before loading, and reported: a first run on a model that
    # is not on disk yet otherwise sits silent for a minute with no
    # indication that anything is happening.
    ensure_weights(
        model_id,
        get_settings().resolved_model_weights_dir(),
        report=lambda message: report(0.0, message),
    )

    report(0.0, "Loading detector")
    detector = get_detector(model_id)
    # At the source's own rate the tracker's lost-track buffer has to
    # be sized for that rate too, or it forgets a vehicle after a
    # fraction of a second.
    tracker = create_tracker(frame_rate=source.fps if every_frame else target_fps)

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
            every_frame=every_frame,
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


def run_train_job(db: Session, params: dict, report: ProgressReporter) -> dict:
    """Train a model on a dataset version.

    Runs in the detached worker, like detection: a training run is
    tens of minutes to hours, and the app window closing must not
    take it with it.
    """
    run_id = params["training_run_id"]
    run = db.get(TrainingRun, run_id)
    if run is None:
        raise NotFoundError(f"Training run not found: {run_id}")

    project = db.get(Project, run.project_id)
    if project is None:
        raise NotFoundError(f"Project not found: {run.project_id}")
    version = db.get(DatasetVersion, run.dataset_version_id)
    if version is None:
        raise NotFoundError(f"Dataset version not found: {run.dataset_version_id}")

    workspace = Path(project.workspace_path)
    weights_dir = get_settings().resolved_model_weights_dir()

    run.status = "running"
    db.commit()

    report(0.0, f"Fetching {run.base_model_id}")
    weights = training.base_weights(run.base_model_id, weights_dir)

    def on_epoch(progress: training.TrainingProgress) -> None:
        # Written to the row as well as the progress file: the file is
        # how the UI follows a live run, the row is what survives the
        # app being closed and reopened.
        run.last_epoch = progress.epoch
        if progress.map50 is not None:
            run.best_map50 = max(run.best_map50 or 0.0, progress.map50)
        db.commit()
        report(progress.fraction(), progress.message())

    report(0.0, f"Training on v{version.version} from {run.base_model_id}")
    try:
        best = train(
            weights=weights,
            data_yaml=training.dataset_yaml(workspace, version),
            output_dir=training.run_directory(workspace, run.id),
            epochs=run.epochs,
            image_size=run.image_size,
            device=get_settings().device,
            on_epoch=on_epoch,
        )
    except BaseException as exc:
        # The row has to reflect it, or the GPU stays held against
        # every future run by a row that claims to be training.
        run.status = "failed"
        run.error_message = str(exc)[:2048]
        run.completed_at = _utcnow()
        db.commit()
        raise

    report(0.97, "Adding the trained model")
    model_id = training.adopt_weights(best, weights_dir, run, version.version)

    run.output_model_id = model_id
    run.status = "completed"
    run.completed_at = _utcnow()
    db.commit()

    return {"training_run_id": run.id, "model_id": model_id, "project_id": project.id}


def settle_train_job(db: Session, params: dict, outcome: str) -> None:
    """Leave an abandoned training run in an honest state.

    Checkpoints already written stay where they are - a cancelled run
    at epoch 60 has produced something, and deleting it would throw
    away an hour of GPU time the user paid for.
    """
    run_id = params.get("training_run_id")
    if run_id is None:
        return
    training.settle(
        db,
        run_id,
        outcome,
        "Stopped before it finished. Any checkpoints it had already written are in the run's folder.",
    )


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


def run_select_job(db: Session, params: dict, report: ProgressReporter) -> dict:
    """Decide which of a source's frames are worth labelling.

    Runs as a job because it decodes every sampled frame to read its
    brightness and perceptual hash - the two signals that cannot come
    from the database. Quality and vehicle count do come from the
    database, because detection already worked them out.

    Frames a human has already touched are left alone. Selection is a
    suggestion about what to look at next; it does not get to overrule
    someone who has looked.
    """
    source_id = params["source_id"]
    source = db.get(Source, source_id)
    if source is None:
        raise NotFoundError(f"Source not found: {source_id}")
    if db.get(Project, source.project_id) is None:
        raise NotFoundError(f"Project not found: {source.project_id}")

    frames = list(
        db.scalars(
            select(Frame)
            .where(Frame.source_id == source_id, Frame.status.in_(("pending", "skipped")))
            .order_by(Frame.frame_index)
        )
    )
    if not frames:
        return {"source_id": source_id, "considered": 0, "selected": 0}

    candidates_by_frame: dict[str, list] = {}
    for candidate in db.scalars(
        select(FrameCandidate)
        .join(Frame, FrameCandidate.frame_id == Frame.id)
        .where(Frame.source_id == source_id)
    ):
        candidates_by_frame.setdefault(candidate.frame_id, []).append(candidate)

    report(0.0, f"Reading {len(frames)} frames")
    signals: list[FrameSignals] = []
    for done, (frame, image) in enumerate(_decode(frames, source), start=1):
        candidates = candidates_by_frame.get(frame.id, [])
        signals.append(
            FrameSignals(
                frame_id=frame.id,
                frame_index=frame.frame_index,
                quality=frame_quality(candidates),
                vehicle_count=len(candidates),
                brightness=brightness_of(image),
                phash=perceptual_hash(image),
            )
        )
        if done % PROGRESS_EVERY == 0 or done == len(frames):
            report(READING_SHARE * done / len(frames), f"Frame {done} of {len(frames)}")

    report(READING_SHARE, "Choosing frames")
    by_id = {frame.id: frame for frame in frames}
    selected = 0
    for decision in select_frames(signals):
        frame = by_id[decision.frame_id]
        frame.selection_reason = decision.reason
        frame.status = "pending" if decision.selected else "skipped"
        selected += 1 if decision.selected else 0
    db.commit()

    return {"source_id": source_id, "considered": len(signals), "selected": selected}


def _decode(frames: list[Frame], source: Source):
    """Yield each frame with its pixels, in one ordered pass.

    Deliberately does not go through ``materialize_frames``: that caches
    every frame it decodes, and selection looks at each frame once and
    then throws the pixels away. Caching them would write a gigabyte of
    JPEGs per source for a computation that needs none of them - and on
    a nearly-full disk that is the difference between a slow job and a
    failed one. Frames are cached when somebody actually opens one to
    label it.
    """
    by_index = {frame.frame_index: frame for frame in frames}
    sampled = [SampledFrame(frame_index=f.frame_index, timestamp_ms=f.timestamp_ms) for f in frames]
    for sampled_frame, image in decode_sampled_frames(Path(source.path_or_uri), sampled):
        yield by_index[sampled_frame.frame_index], image


HANDLERS: dict[str, JobHandler] = {
    "detect": JobHandler(run=run_detect_job, on_abort=settle_detect_job),
    "select": JobHandler(run=run_select_job),
    "train": JobHandler(run=run_train_job, on_abort=settle_train_job),
}
