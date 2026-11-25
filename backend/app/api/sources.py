import shutil
import tempfile
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.api.projects import get_project_or_404
from app.core.config import get_settings
from app.core.errors import AppError, ConflictError, NotFoundError
from app.db.models.frame import Frame
from app.db.models.processing_run import ProcessingRun
from app.db.models.source import Source
from app.db.session import get_db
from app.services.run_estimate import estimate_run
from app.ml.models import DEFAULT_MODEL_ID, get_model
from app.schemas.evaluation import FreezeSourceRequest
from app.schemas.job import JobRead, JobSubmitted
from app.schemas.processing_run import (
    ProcessingRunCreate,
    RunEstimateRead,
    ProcessingRunRead,
    ProcessingRunResult,
    SampledFrameRead,
)
from app.schemas.source import (
    DatasetImportRead,
    DatasetImportRequest,
    SourceContentsRead,
    SourceImportRequest,
    SourceRead,
    SourceRename,
)
from app.services import dataset_import, source_deletion
from app.services.frame_sampler import FrameDecodeError, decode_sampled_frames, sample_frame_timestamps
from app.services.jobs.runner import Launcher, get_launcher, submit_job
from app.services.video_probe import VideoProbeError, probe_video

router = APIRouter(prefix="/projects/{project_id}/sources", tags=["sources"])
#: Runs are addressable on their own now that processing is a background
#: job - the submitting request no longer carries the outcome.
runs_router = APIRouter(prefix="/processing-runs", tags=["sources"])


def _unused_path(dest_dir: Path, name: str) -> Path:
    """``name`` in ``dest_dir``, or a suffixed one if that is taken -
    two clips called ``camera1.mp4`` must not overwrite each other."""
    dest_path = dest_dir / name
    if dest_path.exists():
        dest_path = dest_dir / f"{dest_path.stem}-{uuid.uuid4().hex[:8]}{dest_path.suffix}"
    return dest_path


def _copy_into_workspace(source_path: Path, workspace_path: Path) -> Path:
    dest_dir = workspace_path / "source"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = _unused_path(dest_dir, source_path.name)
    shutil.copy2(source_path, dest_path)
    return dest_path


def _add_video_source(db: Session, project_id: str, stored_path: Path, metadata=None) -> Source:
    """A source for a video already in the workspace, probed for what it
    is unless that was done before it was copied there."""
    metadata = metadata or probe_video(stored_path)
    source = Source(
        project_id=project_id,
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


@router.post("", response_model=SourceRead, status_code=201)
def import_source(project_id: str, payload: SourceImportRequest, db: Session = Depends(get_db)) -> Source:
    project = get_project_or_404(db, project_id)

    original_path = Path(payload.path)
    if not original_path.is_file():
        raise VideoProbeError(f"Source video not found: {original_path}", code="source_not_found")

    # Probed before copying, so a file that is not a video is refused
    # without first being copied into the workspace.
    metadata = probe_video(original_path)
    stored_path = _copy_into_workspace(original_path, Path(project.workspace_path))
    return _add_video_source(db, project.id, stored_path, metadata)


#: Uploads past these are refused while they stream in, before they can
#: fill the disk. A week of one camera's dataset is ~100 MB; a day of
#: 1080p gantry video is a few GB.
MAX_DATASET_UPLOAD_BYTES = 4 * 1024**3
MAX_VIDEO_UPLOAD_BYTES = 20 * 1024**3


def _upload_name(filename: str, default: str) -> str:
    """The file's own name, without whatever folders the client sent -
    and never ``..``, which as a file name is the folder above."""
    name = filename.replace("\\", "/").split("/")[-1].strip()
    return default if name in ("", ".", "..") else name


async def _receive_body(request: Request, target: Path, limit: int, name: str) -> None:
    """Stream the request body into ``target``, refusing it past ``limit``."""
    received = 0
    with target.open("wb") as handle:
        async for chunk in request.stream():
            received += len(chunk)
            if received > limit:
                raise AppError(
                    f"{name} is larger than the {limit // 1024**3} GB an upload can be. "
                    "Split it, or copy it to the server and import it by path.",
                    code="upload_too_large",
                    status_code=413,
                )
            handle.write(chunk)


@router.post("/upload", response_model=SourceRead, status_code=201)
async def upload_source(
    project_id: str,
    request: Request,
    filename: str = Query(..., min_length=1, max_length=255),
    db: Session = Depends(get_db),
) -> Source:
    """Import a video from the user's own computer, sent as the request body.

    The path import only reaches files already on the server, which in
    a browser means copying footage there first. This lands the upload
    where a path import copies its file - the workspace's source folder -
    so probing, processing and deleting are the same from then on. A
    file that turns out not to be a video is removed again.
    """
    project = await run_in_threadpool(get_project_or_404, db, project_id)
    dest_dir = Path(project.workspace_path) / "source"
    dest_dir.mkdir(parents=True, exist_ok=True)
    name = _upload_name(filename, default="video.mp4")
    stored_path = _unused_path(dest_dir, name)
    try:
        await _receive_body(request, stored_path, MAX_VIDEO_UPLOAD_BYTES, name)
        return await run_in_threadpool(_add_video_source, db, project.id, stored_path)
    except BaseException:
        stored_path.unlink(missing_ok=True)
        raise


def _dataset_import_read(result: dataset_import.DatasetImportResult) -> DatasetImportRead:
    return DatasetImportRead(
        source=SourceRead.model_validate(result.source).model_copy(
            update={"stored_frames": result.images_imported}
        ),
        images_imported=result.images_imported,
        boxes_imported=result.boxes_imported,
        already_imported=result.already_imported,
        unreadable=result.unreadable,
        repeated_spot_frames=result.repeated_spot_frames,
        low_confidence_frames=result.low_confidence_frames,
        class_mapping=result.class_mapping,
    )


@router.post("/dataset", response_model=DatasetImportRead, status_code=201)
def import_dataset(project_id: str, payload: DatasetImportRequest, db: Session = Depends(get_db)) -> DatasetImportRead:
    """Import a YOLO dataset on the server - a ``.zip`` or its folder -
    as a source whose frames wait for review with the model's boxes on
    them. See ``services/dataset_import.py``."""
    project = get_project_or_404(db, project_id)
    result = dataset_import.import_from_path(db, project, Path(payload.path))
    db.commit()
    return _dataset_import_read(result)


@router.post("/dataset/upload", response_model=DatasetImportRead, status_code=201)
async def upload_dataset(
    project_id: str,
    request: Request,
    filename: str = Query(..., min_length=1, max_length=255),
    db: Session = Depends(get_db),
) -> DatasetImportRead:
    """Import a dataset ``.zip`` sent as the request body.

    The raw body rather than a multipart form: it is one file, the
    server needs no form parser for it, and a browser streams a File
    straight in. ``filename`` is what the source is called.
    """
    project = await run_in_threadpool(get_project_or_404, db, project_id)
    workspace = Path(project.workspace_path)
    workspace.mkdir(parents=True, exist_ok=True)
    name = _upload_name(filename, default="dataset.zip")

    with tempfile.NamedTemporaryFile(prefix="dataset-upload-", suffix=".zip", dir=workspace, delete=False) as handle:
        upload = Path(handle.name)
    try:
        await _receive_body(request, upload, MAX_DATASET_UPLOAD_BYTES, name)

        def _import() -> DatasetImportRead:
            result = dataset_import.import_archive(db, project, upload, display_name=name)
            db.commit()
            return _dataset_import_read(result)

        return await run_in_threadpool(_import)
    finally:
        upload.unlink(missing_ok=True)


@router.get("", response_model=list[SourceRead])
def list_sources(project_id: str, db: Session = Depends(get_db)) -> list[SourceRead]:
    get_project_or_404(db, project_id)
    sources = list(db.scalars(select(Source).where(Source.project_id == project_id).order_by(Source.created_at.desc())))
    running_source_ids = set(db.scalars(select(ProcessingRun.source_id).where(ProcessingRun.status == "running")))

    # One grouped count rather than one query per source: what the
    # list needs is "does this hold anything", and asking per row
    # turns a five-camera project into six round trips.
    #
    # Only frames that can be opened. A live detection records a frame
    # row whether or not its image was written, and an image that was
    # never written (a full disk, most often) can never be had again -
    # so counting those said "117 frames saved" beside a Label queue
    # with nothing in it. Same rule as the queue itself.
    held = dict(
        db.execute(
            select(Frame.source_id, func.count(Frame.id))
            .join(Source, Frame.source_id == Source.id)
            .where(Frame.source_id.in_([s.id for s in sources]))
            .where(or_(Source.type != "rtsp", Frame.image_path.is_not(None)))
            .group_by(Frame.source_id)
        ).all()
    )

    return [
        SourceRead.model_validate(source).model_copy(
            update={
                "is_processing": source.id in running_source_ids,
                "stored_frames": held.get(source.id, 0),
            }
        )
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


@router.patch("/{source_id}", response_model=SourceRead)
def rename_source(project_id: str, source_id: str, payload: SourceRename, db: Session = Depends(get_db)) -> Source:
    """Give a source a name people recognise. Empty clears it."""
    get_project_or_404(db, project_id)
    source = _get_source_or_404(db, project_id, source_id)
    name = payload.name.strip()
    if len(name) > 128:
        raise ConflictError("A source name can be at most 128 characters.", code="name_too_long")
    source.name = name or None
    db.commit()
    db.refresh(source)
    return source


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


@router.get("/{source_id}/process/estimate", response_model=RunEstimateRead)
def estimate_process_run(
    project_id: str,
    source_id: str,
    target_fps: float = 5.0,
    every_frame: bool = False,
    db: Session = Depends(get_db),
) -> RunEstimateRead:
    """What a run over this source would process and cost.

    Asked before the button is pressed, because the alternative is
    finding out at 80% on a disk that has run out.
    """
    project = get_project_or_404(db, project_id)
    source = _get_source_or_404(db, project_id, source_id)
    estimate = estimate_run(
        source, Path(project.workspace_path), target_fps=target_fps, every_frame=every_frame
    )
    return RunEstimateRead(**asdict(estimate))


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

    # Refused here rather than discovered at 80% with the disk full
    # and a half-written run to clean up.
    estimate = estimate_run(
        source,
        Path(project.workspace_path),
        target_fps=payload.sampling_config.target_fps,
        every_frame=payload.sampling_config.every_frame,
    )
    if not estimate.fits:
        raise ConflictError(estimate.reason or "Not enough space for this run.", code="not_enough_space")

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
