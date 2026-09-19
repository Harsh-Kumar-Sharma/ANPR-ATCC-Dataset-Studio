import shutil
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.projects import get_project_or_404
from app.core.config import get_settings
from app.core.errors import ConflictError, NotFoundError
from app.db.models.processing_run import ProcessingRun
from app.db.models.source import Source
from app.db.session import get_db
from app.ml.models import DEFAULT_MODEL_ID, get_model
from app.schemas.evaluation import FreezeSourceRequest
from app.schemas.job import JobRead, JobSubmitted
from app.schemas.processing_run import (
    ProcessingRunCreate,
    ProcessingRunRead,
    ProcessingRunResult,
    SampledFrameRead,
)
from app.schemas.source import SourceContentsRead, SourceImportRequest, SourceRead
from app.services import source_deletion
from app.services.frame_sampler import FrameDecodeError, decode_sampled_frames, sample_frame_timestamps
from app.services.jobs.runner import Launcher, get_launcher, submit_job
from app.services.video_probe import VideoProbeError, probe_video

router = APIRouter(prefix="/projects/{project_id}/sources", tags=["sources"])
#: Runs are addressable on their own now that processing is a background
#: job - the submitting request no longer carries the outcome.
runs_router = APIRouter(prefix="/processing-runs", tags=["sources"])


def _copy_into_workspace(source_path: Path, workspace_path: Path) -> Path:
    dest_dir = workspace_path / "source"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / source_path.name
    if dest_path.exists():
        dest_path = dest_dir / f"{source_path.stem}-{uuid.uuid4().hex[:8]}{source_path.suffix}"
    shutil.copy2(source_path, dest_path)
    return dest_path


@router.post("", response_model=SourceRead, status_code=201)
def import_source(project_id: str, payload: SourceImportRequest, db: Session = Depends(get_db)) -> Source:
    project = get_project_or_404(db, project_id)

    original_path = Path(payload.path)
    if not original_path.is_file():
        raise VideoProbeError(f"Source video not found: {original_path}", code="source_not_found")

    metadata = probe_video(original_path)
    stored_path = _copy_into_workspace(original_path, Path(project.workspace_path))

    source = Source(
        project_id=project.id,
        type="video",
        path_or_uri=str(stored_path),
        fps=metadata.fps,
        width=metadata.width,
        height=metadata.height,
        duration_ms=metadata.duration_ms,
        frame_count=metadata.frame_count,
    )
    db.add(source)
    db.commit()
    db.refresh(source)
    return source


@router.get("", response_model=list[SourceRead])
def list_sources(project_id: str, db: Session = Depends(get_db)) -> list[SourceRead]:
    get_project_or_404(db, project_id)
    sources = list(db.scalars(select(Source).where(Source.project_id == project_id).order_by(Source.created_at.desc())))
    running_source_ids = set(db.scalars(select(ProcessingRun.source_id).where(ProcessingRun.status == "running")))
    return [
        SourceRead.model_validate(source).model_copy(update={"is_processing": source.id in running_source_ids})
        for source in sources
    ]


def _get_source_or_404(db: Session, project_id: str, source_id: str) -> Source:
    source = db.get(Source, source_id)
    if source is None or source.project_id != project_id:
        raise NotFoundError(f"Source not found: {source_id}")
    return source


@router.get("/{source_id}", response_model=SourceRead)
def get_source(project_id: str, source_id: str, db: Session = Depends(get_db)) -> Source:
    get_project_or_404(db, project_id)
    return _get_source_or_404(db, project_id, source_id)


@router.get("/{source_id}/contents", response_model=SourceContentsRead)
def get_source_contents(project_id: str, source_id: str, db: Session = Depends(get_db)) -> SourceContentsRead:
    """What deleting this source would destroy.

    Its own endpoint for the same reason the project's is: a
    confirmation that cannot say what is about to go is not a
    confirmation, and adding up the files on disk is slow enough that
    the delete request should not be the first time anyone pays for it.
    """
    project = get_project_or_404(db, project_id)
    source = _get_source_or_404(db, project_id, source_id)
    contents = source_deletion.summarize(db, source, Path(project.workspace_path))
    return SourceContentsRead(**asdict(contents))


@router.delete("/{source_id}", response_model=SourceContentsRead)
def remove_source(project_id: str, source_id: str, db: Session = Depends(get_db)) -> SourceContentsRead:
    """Delete a source and everything derived from it, and say what went.

    Refused while a job or a live capture is running against it. A
    dataset version already exported is left alone: it is immutable,
    it belongs to the project rather than to one source, and something
    may already have trained on it.
    """
    project = get_project_or_404(db, project_id)
    source = _get_source_or_404(db, project_id, source_id)
    contents, owned = source_deletion.delete_source(db, source, Path(project.workspace_path))
    db.commit()
    # Only after the commit. Files left behind can be deleted by hand;
    # rows pointing at images that are gone cannot be reasoned about.
    contents.files_removed = source_deletion.remove_source_files(owned)
    return SourceContentsRead(**asdict(contents))


@router.put("/{source_id}/freeze", response_model=SourceRead)
def freeze_source(
    project_id: str, source_id: str, payload: FreezeSourceRequest, db: Session = Depends(get_db)
) -> Source:
    """Mark a source as a frozen validation clip with a manually-
    counted ground truth vehicle total, so detection recall can be
    measured for real (docs/02_IMPLEMENTATION_PLAN.md Phase 7:
    "frozen validation clips" + "detection recall"). The count is a
    one-time human judgment made by watching the raw video - it is
    not derived from anything the pipeline itself produced."""
    get_project_or_404(db, project_id)
    source = _get_source_or_404(db, project_id, source_id)
    source.is_frozen = True
    source.ground_truth_vehicle_count = payload.ground_truth_vehicle_count
    db.commit()
    db.refresh(source)
    return source


@router.post("/{source_id}/sample", response_model=ProcessingRunResult, status_code=201)
def sample_source(
    project_id: str, source_id: str, payload: ProcessingRunCreate, db: Session = Depends(get_db)
) -> ProcessingRunResult:
    """Sample deterministic frame timestamps from a source (Phase 1 scope).

    No detection/tracking runs here yet - that starts in Phase 2. This
    proves a source video can be imported and sampled with reproducible
    source timestamps (docs/02_IMPLEMENTATION_PLAN.md Phase 1 DoD).
    """
    get_project_or_404(db, project_id)
    source = _get_source_or_404(db, project_id, source_id)

    run = ProcessingRun(
        source_id=source.id,
        sampling_config=payload.sampling_config.model_dump(),
        status="running",
    )
    db.add(run)
    db.flush()

    try:
        sampled = sample_frame_timestamps(
            frame_count=source.frame_count,
            native_fps=source.fps,
            target_fps=payload.sampling_config.target_fps,
        )
        # Decode each sampled frame to prove the timestamps are not just
        # arithmetic but actually addressable in the source video.
        for _ in decode_sampled_frames(Path(source.path_or_uri), sampled):
            pass
    except FrameDecodeError as exc:
        run.status = "failed"
        run.error_message = exc.message
        run.completed_at = datetime.now(timezone.utc)
        db.commit()
        raise

    run.status = "completed"
    run.sampled_frame_count = len(sampled)
    run.completed_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(run)

    return ProcessingRunResult(
        run=ProcessingRunRead.model_validate(run),
        sampled_frames=[SampledFrameRead(frame_index=s.frame_index, timestamp_ms=s.timestamp_ms) for s in sampled],
    )


@router.post("/{source_id}/process", response_model=JobSubmitted, status_code=202)
def process_source_endpoint(
    project_id: str,
    source_id: str,
    payload: ProcessingRunCreate,
    db: Session = Depends(get_db),
    launcher: Launcher = Depends(get_launcher),
) -> JobSubmitted:
    """Submit a detect-and-track run over a source video.

    Returns as soon as the work is queued. This used to run inline and
    block the request for one to three minutes on real footage, with no
    progress and no way to cancel; poll ``GET /jobs/{id}`` or subscribe
    to ``GET /jobs/{id}/progress`` to follow it.

    The processing run row is created here rather than in the worker so
    the caller gets a run id immediately, instead of having to wait for
    a process to start before it can link to anything.
    """
    project = get_project_or_404(db, project_id)
    source = _get_source_or_404(db, project_id, source_id)

    already_running = db.scalar(
        select(ProcessingRun).where(ProcessingRun.source_id == source.id, ProcessingRun.status.in_(("pending", "running")))
    )
    if already_running is not None:
        raise ConflictError(
            "A processing run is already in progress for this source. "
            "Wait for it to finish or cancel it before starting another.",
            code="processing_already_running",
        )

    # Checked here rather than in the worker: an unknown model is the
    # caller's mistake, and answering it two minutes later through a
    # failed job is a poor way to report a typo.
    model_id = payload.model_id or DEFAULT_MODEL_ID
    get_model(model_id, get_settings().resolved_model_weights_dir())

    run = ProcessingRun(
        source_id=source.id,
        sampling_config=payload.sampling_config.model_dump(),
        tracker_config={"frame_rate": payload.sampling_config.target_fps},
        # Written now so the run says which model it *asked* for even
        # if the worker never gets to load it. The worker overwrites it
        # with what actually ran.
        detector_version=model_id,
        status="pending",
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    job = submit_job(
        db,
        type="detect",
        project_id=project.id,
        params={"run_id": run.id, "model_id": model_id},
        launcher=launcher,
    )

    return JobSubmitted(job=JobRead.model_validate(job), run_id=run.id)


@router.post("/{source_id}/select-frames", response_model=JobSubmitted, status_code=202)
def select_frames_endpoint(
    project_id: str,
    source_id: str,
    db: Session = Depends(get_db),
    launcher: Launcher = Depends(get_launcher),
) -> JobSubmitted:
    """Decide which of this source's frames are worth labelling.

    Runs in the background because it decodes every sampled frame.
    Frames a human has already labelled or rejected are left alone -
    selection suggests what to look at next, it does not overrule
    someone who has already looked.
    """
    project = get_project_or_404(db, project_id)
    source = _get_source_or_404(db, project_id, source_id)

    job = submit_job(db, type="select", project_id=project.id, params={"source_id": source.id}, launcher=launcher)
    return JobSubmitted(job=JobRead.model_validate(job))


@runs_router.get("/{run_id}", response_model=ProcessingRunRead)
def get_processing_run(run_id: str, db: Session = Depends(get_db)) -> ProcessingRun:
    """Read a processing run's current state.

    Needed once processing became a background job: ``/process`` returns
    before the run has done anything, so the run's status, frame count
    and error have to be readable afterwards.
    """
    run = db.get(ProcessingRun, run_id)
    if run is None:
        raise NotFoundError(f"Processing run not found: {run_id}")
    return run
