from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.class_schema import is_valid_class_id
from app.core.errors import AppError, NotFoundError
from app.db.models.annotation import Annotation
from app.db.models.frame_candidate import FrameCandidate
from app.db.models.processing_run import ProcessingRun
from app.db.models.project import Project
from app.db.models.source import Source
from app.db.models.track import Track
from app.schemas.review import TrackReviewRequest


class InvalidReviewError(AppError):
    code = "invalid_review"


def get_project_for_track(db: Session, track_id: str) -> Project | None:
    return db.scalar(
        select(Project)
        .join(Source, Source.project_id == Project.id)
        .join(ProcessingRun, ProcessingRun.source_id == Source.id)
        .join(Track, Track.run_id == ProcessingRun.id)
        .where(Track.id == track_id)
    )


def submit_review(db: Session, track: Track, payload: TrackReviewRequest) -> Annotation:
    """Apply a human review decision to a track.

    Upserts the track's single active human annotation (editing which
    frame/class/bbox is chosen updates the same row - see
    docs/DECISIONS.md D-003: human truth is authoritative and distinct
    from model output, and re-review must not fork into duplicates).
    Never touches the frame_candidates row it points to, or any other
    track - only this track's own human judgment changes.
    """
    frame_candidate = db.get(FrameCandidate, payload.frame_candidate_id)
    if frame_candidate is None or frame_candidate.track_id != track.id:
        raise NotFoundError(f"Frame candidate not found on this track: {payload.frame_candidate_id}")

    if payload.class_id is not None:
        project = get_project_for_track(db, track.id)
        schema_version = project.class_schema_version if project else "v1"
        if not is_valid_class_id(schema_version, payload.class_id):
            raise InvalidReviewError(f"class_id {payload.class_id} is not valid for class schema {schema_version}")

    bbox_json = payload.bbox_json if payload.bbox_json is not None else list(frame_candidate.bbox_json)

    existing = db.scalar(
        select(Annotation)
        .join(FrameCandidate, Annotation.frame_candidate_id == FrameCandidate.id)
        .where(FrameCandidate.track_id == track.id, Annotation.source == "human")
    )

    if existing is not None:
        existing.frame_candidate_id = frame_candidate.id
        existing.class_id = payload.class_id
        existing.bbox_json = bbox_json
        existing.status = payload.decision
        existing.updated_at = datetime.now(timezone.utc)
        annotation = existing
    else:
        annotation = Annotation(
            frame_candidate_id=frame_candidate.id,
            source="human",
            class_id=payload.class_id,
            bbox_json=bbox_json,
            status=payload.decision,
        )
        db.add(annotation)

    track.review_status = payload.decision

    db.commit()
    db.refresh(annotation)
    db.refresh(track)
    return annotation


def get_human_annotation(db: Session, track_id: str) -> Annotation | None:
    return db.scalar(
        select(Annotation)
        .join(FrameCandidate, Annotation.frame_candidate_id == FrameCandidate.id)
        .where(FrameCandidate.track_id == track_id, Annotation.source == "human")
    )
