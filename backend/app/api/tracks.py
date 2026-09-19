from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.projects import get_project_or_404
from app.core.errors import NotFoundError
from app.db.models.frame_candidate import FrameCandidate
from app.db.models.ocr_candidate import OcrCandidate
from app.db.models.processing_run import ProcessingRun
from app.db.models.source import Source
from app.db.models.track import Track
from app.db.session import get_db
from app.ml.factory import get_default_ocr_engine
from app.ml.plate_ocr import PlateOcrEngine
from app.schemas.ocr import OcrCandidateRead, PlateTextWrite
from app.schemas.review import AnnotationRead, TrackReviewRequest, TrackReviewResult
from app.schemas.track import FrameCandidateRead, TrackRead, TrackTimeline
from app.services.ocr_processor import run_ocr_for_track
from app.services.plate_text import set_track_plate_text
from app.services.review import get_human_annotation, submit_review

project_tracks_router = APIRouter(prefix="/projects/{project_id}/tracks", tags=["tracks"])
tracks_router = APIRouter(prefix="/tracks", tags=["tracks"])


def _get_track_or_404(db: Session, track_id: str) -> Track:
    track = db.get(Track, track_id)
    if track is None:
        raise NotFoundError(f"Track not found: {track_id}")
    return track


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
    track = _get_track_or_404(db, track_id)

    frames = list(
        db.scalars(select(FrameCandidate).where(FrameCandidate.track_id == track_id).order_by(FrameCandidate.frame_index))
    )
    return TrackTimeline(
        track=TrackRead.model_validate(track),
        frames=[FrameCandidateRead.model_validate(f) for f in frames],
    )


@tracks_router.get("/frames/{frame_candidate_id}/image")
def get_frame_image(frame_candidate_id: str, db: Session = Depends(get_db)) -> FileResponse:
    """Serve a frame candidate's saved crop - the review UI's <img> src.
    Frame images live on the backend's local disk, not in the renderer."""
    frame = db.get(FrameCandidate, frame_candidate_id)
    if frame is None:
        raise NotFoundError(f"Frame candidate not found: {frame_candidate_id}")
    return FileResponse(frame.image_path, media_type="image/jpeg")


@tracks_router.put("/{track_id}/review", response_model=TrackReviewResult)
def review_track(track_id: str, payload: TrackReviewRequest, db: Session = Depends(get_db)) -> TrackReviewResult:
    """Submit (or update) the human review decision for a track:
    which frame/class/bbox is the human truth, and whether the track
    is accepted, hard, or failed. See docs/06_UI_UX_SPEC.md Screen 4
    Required Actions."""
    track = _get_track_or_404(db, track_id)
    annotation = submit_review(db, track, payload)
    return TrackReviewResult(
        track=TrackRead.model_validate(track),
        annotation=AnnotationRead.model_validate(annotation),
    )


@tracks_router.get("/{track_id}/annotation", response_model=AnnotationRead)
def get_track_annotation(track_id: str, db: Session = Depends(get_db)) -> AnnotationRead:
    """The track's current human annotation, if any - lets the review
    UI restore prior review state when a project is reopened."""
    _get_track_or_404(db, track_id)
    annotation = get_human_annotation(db, track_id)
    if annotation is None:
        raise NotFoundError(f"No human annotation yet for track: {track_id}")
    return AnnotationRead.model_validate(annotation)


@tracks_router.post("/{track_id}/ocr", response_model=list[OcrCandidateRead])
def run_track_ocr(
    track_id: str,
    db: Session = Depends(get_db),
    engine: PlateOcrEngine = Depends(get_default_ocr_engine),
) -> list[OcrCandidate]:
    """Run OCR on the track's OCR-candidate frames (Phase 3's
    shortlist), best quality first, stopping once a confident reading
    is found. See docs/07_ML_CV_PIPELINE.md OCR steps 1-7."""
    track = _get_track_or_404(db, track_id)
    return run_ocr_for_track(db, track, engine)


@tracks_router.get("/{track_id}/ocr-candidates", response_model=list[OcrCandidateRead])
def list_track_ocr_candidates(track_id: str, db: Session = Depends(get_db)) -> list[OcrCandidate]:
    """Every OCR attempt made for this track, most recent first -
    all attempts stay traceable, never overwritten."""
    _get_track_or_404(db, track_id)
    return list(
        db.scalars(select(OcrCandidate).where(OcrCandidate.track_id == track_id).order_by(OcrCandidate.created_at.desc()))
    )


@tracks_router.put("/{track_id}/plate-text", response_model=AnnotationRead)
def set_plate_text(track_id: str, payload: PlateTextWrite, db: Session = Depends(get_db)) -> Annotation:
    """Record what a human read off this track's plate.

    It goes on the annotation, which is where everything else known
    about the vehicle in that box already lives. It used to go into
    ``ocr_candidates`` as a human-sourced row, which no export ever
    read - so the reading looked saved and never left the database.

    409 when the track has not been reviewed: there is no label to put a
    plate on yet, and that is worth saying rather than writing the
    reading somewhere it will be lost.
    """
    track = _get_track_or_404(db, track_id)
    annotation = set_track_plate_text(db, track, payload.plate_text)
    db.commit()
    db.refresh(annotation)
    return annotation
