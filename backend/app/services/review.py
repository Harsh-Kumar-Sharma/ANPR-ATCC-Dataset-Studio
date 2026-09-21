from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError, NotFoundError
from app.db.models.annotation import Annotation
from app.db.models.frame import Frame
from app.db.models.frame_candidate import FrameCandidate
from app.db.models.processing_run import ProcessingRun
from app.db.models.project import Project
from app.db.models.source import Source
from app.db.models.track import Track
from app.schemas.review import TrackReviewRequest
from app.services.class_definitions import is_valid_class_id


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
    if frame_candidate.frame_id is None:
        # Candidates from before full frames were captured have no frame
        # to hang a label on. The replan's answer is to re-run detection
        # rather than migrate them, so say that rather than guess.
        raise InvalidReviewError(
            "This track predates full-frame capture and cannot be labelled. Re-run detection on its source."
        )

    if payload.class_id is not None:
        project = get_project_for_track(db, track.id)
        if project is None or not is_valid_class_id(db, project.id, payload.class_id):
            raise InvalidReviewError(f"class_id {payload.class_id} is not one of this project's classes")

    bbox_json = payload.bbox_json if payload.bbox_json is not None else list(frame_candidate.bbox_json)

    existing = db.scalar(
        select(Annotation)
        .join(FrameCandidate, Annotation.frame_candidate_id == FrameCandidate.id)
        .where(FrameCandidate.track_id == track.id, Annotation.source == "human")
    )

    if existing is not None:
        existing.frame_id = frame_candidate.frame_id
        existing.frame_candidate_id = frame_candidate.id
        existing.class_id = payload.class_id
        existing.bbox_json = bbox_json
        existing.status = payload.decision
        existing.updated_at = datetime.now(timezone.utc)
        annotation = existing
    else:
        annotation = Annotation(
            frame_id=frame_candidate.frame_id,
            frame_candidate_id=frame_candidate.id,
            source="human",
            class_id=payload.class_id,
            bbox_json=bbox_json,
            status=payload.decision,
        )
        db.add(annotation)

    track.review_status = payload.decision

    # Accepting is labelling. The box is on the frame, the class is
    # chosen, a human said yes - and until this line the frame stayed
    # "pending", which is the one state the dataset export refuses to
    # take. Everything the user accepted was being left out of the
    # dataset it was accepted for.
    #
    # Only for accepted work with a class on it. A track marked hard
    # or failed is a judgement about the detection, not a label, and
    # a box with no class exports as "unclassified" anyway.
    frame = db.get(Frame, frame_candidate.frame_id)
    if frame is not None and payload.decision == "accepted" and payload.class_id is not None:
        # Flushed first: this session does not autoflush, so the
        # annotation just added would not be visible to the query
        # below and the frame would never look finished.
        db.flush()
        if _every_detection_reviewed(db, frame):
            frame.status = "labeled"

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


def _every_detection_reviewed(db: Session, frame: Frame) -> bool:
    """Has a human accounted for everything the model found here?

    Accepting a track is judging one detection, not looking at a
    whole frame. A frame with two plates on it and one accepted is
    exactly the "partially labelled" case the export warns about -
    marking it finished would both hide that warning and teach the
    model that the second plate is background.

    So the frame becomes labelled only once nothing the detector
    found on it is left unanswered. Which, for a frame with one
    vehicle in it, is the moment it is accepted.
    """
    detections = db.scalars(select(FrameCandidate.id).where(FrameCandidate.frame_id == frame.id)).all()
    if not detections:
        return False

    answered = set(
        db.scalars(
            select(Annotation.frame_candidate_id).where(
                Annotation.frame_id == frame.id, Annotation.frame_candidate_id.is_not(None)
            )
        )
    )
    return all(detection in answered for detection in detections)
