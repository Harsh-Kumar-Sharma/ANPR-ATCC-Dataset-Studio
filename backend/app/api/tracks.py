from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.projects import get_project_or_404
from app.core.errors import NotFoundError
from app.db.models.frame_candidate import FrameCandidate
from app.db.models.processing_run import ProcessingRun
from app.db.models.source import Source
from app.db.models.track import Track
from app.db.session import get_db
from app.schemas.track import FrameCandidateRead, TrackRead, TrackTimeline

project_tracks_router = APIRouter(prefix="/projects/{project_id}/tracks", tags=["tracks"])
tracks_router = APIRouter(prefix="/tracks", tags=["tracks"])


@project_tracks_router.get("", response_model=list[TrackRead])
def list_tracks_for_project(project_id: str, run_id: str | None = None, db: Session = Depends(get_db)) -> list[Track]:
    get_project_or_404(db, project_id)
    stmt = (
        select(Track)
        .join(ProcessingRun, Track.run_id == ProcessingRun.id)
        .join(Source, ProcessingRun.source_id == Source.id)
        .where(Source.project_id == project_id)
        .order_by(Track.start_ts)
    )
    if run_id is not None:
        stmt = stmt.where(Track.run_id == run_id)
    return list(db.scalars(stmt))


@tracks_router.get("/{track_id}", response_model=TrackTimeline)
def get_track_timeline(track_id: str, db: Session = Depends(get_db)) -> TrackTimeline:
    """The track's ordered frame candidates - what the Phase 4 review
    UI browses per docs/06_UI_UX_SPEC.md Screen 4."""
    track = db.get(Track, track_id)
    if track is None:
        raise NotFoundError(f"Track not found: {track_id}")

    frames = list(
        db.scalars(select(FrameCandidate).where(FrameCandidate.track_id == track_id).order_by(FrameCandidate.frame_index))
    )
    return TrackTimeline(
        track=TrackRead.model_validate(track),
        frames=[FrameCandidateRead.model_validate(f) for f in frames],
    )
