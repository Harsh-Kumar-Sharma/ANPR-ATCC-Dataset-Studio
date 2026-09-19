from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.services.class_definitions import class_names_for
from app.db.models.annotation import Annotation
from app.db.models.frame_candidate import FrameCandidate
from app.db.models.processing_run import ProcessingRun
from app.db.models.source import Source
from app.db.models.track import Track


@dataclass
class QueueItem:
    track_id: str
    review_status: str
    bucket: str | None
    confidence: float
    representative_frame_id: str | None


@dataclass
class DisagreementItem:
    track_id: str
    annotation_id: str
    detector_class: str
    human_class_id: int
    human_class_name: str


def _project_tracks(db: Session, project_id: str) -> list[Track]:
    stmt = (
        select(Track)
        .join(ProcessingRun, Track.run_id == ProcessingRun.id)
        .join(Source, ProcessingRun.source_id == Source.id)
        .where(Source.project_id == project_id)
    )
    return list(db.scalars(stmt))


def _track_frames(db: Session, track_id: str) -> list[FrameCandidate]:
    return list(db.scalars(select(FrameCandidate).where(FrameCandidate.track_id == track_id)))


def _to_queue_item(db: Session, track: Track) -> QueueItem | None:
    frames = _track_frames(db, track.id)
    if not frames:
        return None
    # The track's most confident frame is its strongest evidence - if
    # even that one is low-confidence, the whole track is uncertain.
    best = max(frames, key=lambda f: f.detector_confidence)
    return QueueItem(
        track_id=track.id,
        review_status=track.review_status,
        bucket=track.bucket,
        confidence=best.detector_confidence,
        representative_frame_id=best.id,
    )


def build_low_confidence_queue(db: Session, project_id: str, limit: int = 50) -> list[QueueItem]:
    """Unreviewed tracks, least-confident first - classic uncertainty-
    sampling active learning (docs/02_IMPLEMENTATION_PLAN.md Phase 8):
    review the examples the model is least sure about first."""
    tracks = [t for t in _project_tracks(db, project_id) if t.review_status == "unreviewed"]
    items = [item for t in tracks if (item := _to_queue_item(db, t)) is not None]
    items.sort(key=lambda i: i.confidence)
    return items[:limit]


def build_hard_failed_queue(db: Session, project_id: str, limit: int = 50) -> list[QueueItem]:
    """Every track flagged difficult by either the pipeline
    (``bucket``) or a human (``review_status``) - the whole point of
    never deleting HARD/FAILED evidence since Phase 2 was so this
    queue could eventually exist."""
    tracks = [
        t
        for t in _project_tracks(db, project_id)
        if t.bucket in ("HARD", "FAILED") or t.review_status in ("hard", "failed")
    ]
    items = [item for t in tracks if (item := _to_queue_item(db, t)) is not None]
    items.sort(key=lambda i: i.confidence)
    return items[:limit]


#: Coarse mapping from the detector's COCO-level observation to which
#: ATCC classes are plausible for it. NOT a real model-ensemble
#: disagreement signal - only one trained model exists (yolo26n.pt,
#: COCO-pretrained). This instead flags cases where a human's ATCC
#: classification is grossly inconsistent with what the detector saw
#: at all - worth a second look, whether that means a labeling
#: mistake or a genuinely unusual vehicle. See docs/HANDOFF.md.
COCO_TO_PLAUSIBLE_ATCC_CLASS_IDS: dict[str, set[int]] = {
    "bicycle": {1},
    "motorcycle": {1},
    "car": {4},
    "bus": {7, 8, 9},
    "truck": {5, 6, 10, 11, 12, 13, 14, 15, 20},
}


def find_model_human_disagreements(db: Session, project_id: str) -> list[DisagreementItem]:
    """Where the human's class contradicts what the detector called it.

    Joins through the frame candidate, so it sees only labels written by
    track review. A box drawn on the labelling canvas has no candidate
    and is invisible here - a project labelled entirely on the canvas
    gets an empty queue, which reads as "no problems found" rather than
    "not looked at".

    That is a real gap, not an inherent limit. The frames those boxes sit
    on do carry ``FrameCandidate`` rows with their own boxes, so a canvas
    label could be matched to a detection by overlap and compared the
    same way. It is left undone deliberately: picking an overlap
    threshold is a judgement with its own consequences, and it is a
    ticket rather than a footnote.
    """
    class_names = class_names_for(db, project_id)
    stmt = (
        select(Annotation, FrameCandidate)
        .join(FrameCandidate, Annotation.frame_candidate_id == FrameCandidate.id)
        .join(Track, FrameCandidate.track_id == Track.id)
        .join(ProcessingRun, Track.run_id == ProcessingRun.id)
        .join(Source, ProcessingRun.source_id == Source.id)
        .where(
            Source.project_id == project_id,
            Annotation.source == "human",
            Annotation.status.in_(["accepted", "hard"]),
            Annotation.class_id.is_not(None),
        )
    )

    disagreements = []
    for annotation, frame_candidate in db.execute(stmt).all():
        plausible = COCO_TO_PLAUSIBLE_ATCC_CLASS_IDS.get(frame_candidate.detector_class)
        if plausible is None:
            continue  # unmapped detector class - no basis to flag anything
        if annotation.class_id not in plausible:
            disagreements.append(
                DisagreementItem(
                    track_id=frame_candidate.track_id,
                    annotation_id=annotation.id,
                    detector_class=frame_candidate.detector_class,
                    human_class_id=annotation.class_id,
                    human_class_name=class_names.get(annotation.class_id, str(annotation.class_id)),
                )
            )
    return disagreements
