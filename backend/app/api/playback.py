from pathlib import Path

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.api.projects import get_project_or_404
from app.core.errors import AppError, NotFoundError
from app.db.models.source import Source
from app.db.session import get_db
from app.schemas.detections import DetectionFrameRead, DetectionRunRead, SourceDetectionsRead
from app.services.detection_overlay import detections_by_frame, list_runs_with_track_counts

router = APIRouter(prefix="/projects/{project_id}/sources", tags=["playback"])

# Chromium picks a demuxer by sniffing the bytes, so the MIME type only
# needs to name the right family. 3GP, M4V and MOV are ISO base media
# files - the same container family as MP4.
_VIDEO_MEDIA_TYPES = {
    ".mp4": "video/mp4",
    ".m4v": "video/mp4",
    ".mov": "video/mp4",
    ".3gp": "video/mp4",
    ".3g2": "video/mp4",
    ".webm": "video/webm",
    ".mkv": "video/x-matroska",
    ".avi": "video/x-msvideo",
}


class SourceNotPlayableError(AppError):
    code = "source_not_playable"


def _get_source_or_404(db: Session, project_id: str, source_id: str) -> Source:
    get_project_or_404(db, project_id)
    source = db.get(Source, source_id)
    if source is None or source.project_id != project_id:
        raise NotFoundError(f"Source not found: {source_id}")
    return source


@router.get("/{source_id}/video")
def get_source_video(project_id: str, source_id: str, db: Session = Depends(get_db)) -> FileResponse:
    """Stream the imported video file to the desktop player.

    ``FileResponse`` honours HTTP Range requests, which the ``<video>``
    element depends on to seek without downloading the whole file first.
    The file is served as imported - no transcoding - so playback works
    for whatever the embedded Chromium can decode.
    """
    source = _get_source_or_404(db, project_id, source_id)
    if source.type != "video":
        raise SourceNotPlayableError("Only imported video files can be played back; a live stream has no stored file.")

    path = Path(source.path_or_uri)
    if not path.is_file():
        raise NotFoundError(f"Source video file is missing from the workspace: {path}")
    return FileResponse(path, media_type=_VIDEO_MEDIA_TYPES.get(path.suffix.lower(), "application/octet-stream"))


@router.get("/{source_id}/detections", response_model=SourceDetectionsRead)
def get_source_detections(
    project_id: str, source_id: str, run_id: str | None = None, db: Session = Depends(get_db)
) -> SourceDetectionsRead:
    """Every tracked detection of one processing run, grouped by frame,
    for drawing boxes over video playback.

    Defaults to the newest completed run, and only ever returns one run:
    re-processing a source produces a second, independent set of tracks
    for the same vehicles, and overlaying both would double every box.
    """
    source = _get_source_or_404(db, project_id, source_id)
    runs = list_runs_with_track_counts(db, source.id)

    if run_id is None:
        selected = next((run for run, _ in runs if run.status == "completed"), None)
    else:
        selected = next((run for run, _ in runs if run.id == run_id), None)
        if selected is None:
            raise NotFoundError(f"Processing run {run_id} does not belong to source {source_id}")

    frames = detections_by_frame(db, selected.id) if selected is not None else []
    return SourceDetectionsRead(
        source_id=source.id,
        width=source.width,
        height=source.height,
        fps=source.fps,
        run_id=selected.id if selected is not None else None,
        runs=[
            DetectionRunRead(id=run.id, status=run.status, started_at=run.started_at, track_count=count)
            for run, count in runs
        ],
        frames=[DetectionFrameRead.model_validate(frame) for frame in frames],
    )
