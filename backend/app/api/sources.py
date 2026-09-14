import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.projects import get_project_or_404
from app.core.errors import ConflictError, NotFoundError
from app.db.models.processing_run import ProcessingRun
from app.db.models.source import Source
from app.db.models.track import Track
from app.db.session import get_db
from app.ml.detector import Detector
from app.ml.factory import create_tracker, get_default_detector
from app.schemas.evaluation import FreezeSourceRequest
from app.schemas.processing_run import (
    ProcessingRunCreate,
    ProcessingRunRead,
    ProcessingRunResult,
    SampledFrameRead,
)
from app.schemas.source import SourceImportRequest, SourceRead
from app.schemas.track import ProcessingRunTracksResult, TrackRead
from app.services.frame_sampler import FrameDecodeError, decode_sampled_frames, sample_frame_timestamps
from app.services.track_processor import process_source
from app.services.video_probe import VideoProbeError, probe_video

router = APIRouter(prefix="/projects/{project_id}/sources", tags=["sources"])


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


@router.post("/{source_id}/process", response_model=ProcessingRunTracksResult, status_code=201)
def process_source_endpoint(
    project_id: str,
    source_id: str,
    payload: ProcessingRunCreate,
    db: Session = Depends(get_db),
    detector: Detector = Depends(get_default_detector),
) -> ProcessingRunTracksResult:
    """Detect and track vehicles across a source video (Phase 2 scope).

    Produces persistent tracks with candidate frames
    (docs/02_IMPLEMENTATION_PLAN.md Phase 2 DoD). Runs synchronously
    for now - see docs/HANDOFF.md for the plan to move this to a
    background job once processing time on real footage is known.
    """
    project = get_project_or_404(db, project_id)
    source = _get_source_or_404(db, project_id, source_id)

    already_running = db.scalar(
        select(ProcessingRun).where(ProcessingRun.source_id == source.id, ProcessingRun.status == "running")
    )
    if already_running is not None:
        raise ConflictError(
            "A processing run is already in progress for this source. "
            "Wait for it to finish before starting another.",
            code="processing_already_running",
        )

    tracker_config = {"frame_rate": payload.sampling_config.target_fps}
    tracker = create_tracker(frame_rate=payload.sampling_config.target_fps)

    run = ProcessingRun(
        source_id=source.id,
        sampling_config=payload.sampling_config.model_dump(),
        detector_version=detector.model_version,
        tracker_config=tracker_config,
        status="running",
    )
    db.add(run)
    db.flush()

    try:
        process_source(
            db=db,
            source=source,
            workspace_path=Path(project.workspace_path),
            run=run,
            target_fps=payload.sampling_config.target_fps,
            detector=detector,
            tracker=tracker,
        )
    except FrameDecodeError as exc:
        run.status = "failed"
        run.error_message = exc.message
        run.completed_at = datetime.now(timezone.utc)
        db.commit()
        raise

    run.completed_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(run)

    tracks = list(db.scalars(select(Track).where(Track.run_id == run.id).order_by(Track.start_ts)))

    return ProcessingRunTracksResult(
        run=ProcessingRunRead.model_validate(run),
        tracks=[TrackRead.model_validate(t) for t in tracks],
    )
