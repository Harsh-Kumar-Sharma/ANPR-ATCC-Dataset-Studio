from dataclasses import asdict
from pathlib import Path

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app.api.projects import get_project_or_404
from app.core.errors import NotFoundError
from app.db.models.processing_run import ProcessingRun
from app.db.models.source import Source
from app.db.session import get_db
from app.ml.factory import DetectorProvider, create_tracker, get_detector_provider
from app.ml.models import DEFAULT_MODEL_ID
from app.schemas.rtsp import RtspSessionStatusRead, RtspStartRequest, RtspStartResult
from app.services.rtsp_session import KeepFrames, RtspCaptureSession
from app.services.live_reconcile import UNFINISHED as UNFINISHED_LIVE, settle as settle_live_run
from app.services.rtsp_session_registry import get_session, register_session
from app.services.rtsp_source import ConnectionProvider, RtspSourceAdapter, default_connection_provider

router = APIRouter(prefix="/projects/{project_id}/sources", tags=["rtsp"])
run_router = APIRouter(prefix="/processing-runs", tags=["rtsp"])


def get_rtsp_connection_provider() -> ConnectionProvider:
    """Real by default; overridden in tests to avoid ever attempting a
    real (up to 30s-to-time-out) network connection."""
    return default_connection_provider


@router.post("/rtsp/start", response_model=RtspStartResult, status_code=201)
def start_rtsp_session(
    project_id: str,
    payload: RtspStartRequest,
    db: Session = Depends(get_db),
    detector_provider: DetectorProvider = Depends(get_detector_provider),
    connection_provider: ConnectionProvider = Depends(get_rtsp_connection_provider),
) -> RtspStartResult:
    """Start a live RTSP capture-and-track session
    (docs/02_IMPLEMENTATION_PLAN.md Phase 9). Runs on background
    threads and returns immediately - poll
    ``GET /processing-runs/{run_id}/rtsp/status`` for progress, and
    call ``POST /processing-runs/{run_id}/rtsp/stop`` to end it.

    Unlike an offline video, a live stream has no known frame count or
    duration - ``frame_count``/``duration_ms`` on the created source
    are ``0`` sentinels, not measurements, and ``fps`` is the caller's
    hint (many RTSP cameras don't report a reliable one), not a probed
    value.
    """
    project = get_project_or_404(db, project_id)
    # A live session is where a model you trained earns its keep, so
    # this is not the one place that stays hard-coded. Resolved before
    # anything is written: an unknown model should not leave a source
    # and a run behind.
    detector = detector_provider(payload.model_id or DEFAULT_MODEL_ID)

    source = Source(
        project_id=project.id,
        type="rtsp",
        path_or_uri=payload.rtsp_url,
        fps=payload.expected_fps,
        width=0,
        height=0,
        duration_ms=0,
        frame_count=0,
    )
    db.add(source)
    db.flush()

    run = ProcessingRun(
        source_id=source.id,
        sampling_config={
            "protocol": "rtsp",
            "expected_fps": payload.expected_fps,
            "buffer_maxlen": payload.buffer_maxlen,
            "keep_frames": payload.keep_frames,
            "keep_every": payload.keep_every,
        },
        detector_version=detector.model_version,
        tracker_config={"frame_rate": payload.expected_fps},
        status="running",
    )
    db.add(run)
    db.commit()
    db.refresh(source)
    db.refresh(run)

    adapter = RtspSourceAdapter(connection_factory=lambda: connection_provider(payload.rtsp_url))
    tracker = create_tracker(frame_rate=payload.expected_fps)
    session = RtspCaptureSession(
        run_id=run.id,
        source_id=source.id,
        keep_frames=KeepFrames(
            enabled=payload.keep_frames,
            every=payload.keep_every,
            max_frames=payload.keep_max_frames,
        ),
        adapter=adapter,
        detector=detector,
        tracker=tracker,
        workspace_path=Path(project.workspace_path),
        buffer_maxlen=payload.buffer_maxlen,
    )
    register_session(run.id, session)
    session.start()

    return RtspStartResult(source=source, run=run)


def _get_session_or_404(run_id: str) -> RtspCaptureSession:
    session = get_session(run_id)
    if session is None:
        raise NotFoundError(f"No live (or previously started) RTSP session for run: {run_id}")
    return session


@run_router.get("/{run_id}/rtsp/status", response_model=RtspSessionStatusRead)
def get_rtsp_status(run_id: str) -> RtspSessionStatusRead:
    session = _get_session_or_404(run_id)
    status = session.status()
    return RtspSessionStatusRead(**asdict(status))


@run_router.post("/{run_id}/rtsp/stop", response_model=RtspSessionStatusRead)
def stop_rtsp_session(run_id: str, db: Session = Depends(get_db)) -> RtspSessionStatusRead:
    """Signal the session to stop. Does not block until it actually
    finishes (that can take up to one capture-read cycle plus a final
    persist) - poll ``/rtsp/status`` until ``stopped`` is true.

    A run the registry has never heard of is settled rather than
    refused. That is what an abandoned capture looks like after a
    restart: the threads died with the old process, but the row still
    claims to be running and blocks its source against deletion. The
    user pressing Stop is right, and answering 404 left them with no
    way to act on it.
    """
    session = get_session(run_id)
    if session is not None:
        session.stop()
        return RtspSessionStatusRead(**asdict(session.status()))

    run = db.get(ProcessingRun, run_id)
    if run is None:
        raise NotFoundError(f"No live (or previously started) RTSP session for run: {run_id}")
    if run.status in UNFINISHED_LIVE:
        settle_live_run(db, run)
        db.commit()
    return RtspSessionStatusRead(
        run_id=run_id,
        connected=False,
        reconnect_attempts=0,
        frames_captured=run.sampled_frame_count or 0,
        frames_dropped=0,
        tracks_persisted=0,
        stopped=True,
        error=run.error_message,
    )


@run_router.get("/{run_id}/rtsp/preview.jpg")
def get_rtsp_preview(run_id: str) -> Response:
    """The latest processed frame of a live session with its tracked
    boxes drawn on, for the desktop's live view to poll.

    Returns 204 until the first frame has been processed rather than an
    error, since "not yet" is the normal state for the first second or
    two of every session. ``X-Frame-Sequence`` lets the client skip
    re-rendering a frame it already has.
    """
    session = _get_session_or_404(run_id)
    frame = session.preview()
    if frame is None:
        return Response(status_code=204, headers={"Cache-Control": "no-store"})
    return Response(
        content=frame.jpeg,
        media_type="image/jpeg",
        headers={"Cache-Control": "no-store", "X-Frame-Sequence": str(frame.sequence)},
    )
