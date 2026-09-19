from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.annotation import Annotation
from app.db.models.frame_candidate import FrameCandidate
from app.db.models.processing_run import ProcessingRun
from app.db.models.source import Source
from app.db.models.track import Track
from app.services.annotations import chunked
from app.services.class_definitions import class_names_for
from app.services.dataset_query import project_annotations_for_export
from app.services.track_metrics import iou


@dataclass
class QueueItem:
    track_id: str
    review_status: str
    bucket: str | None
    confidence: float
    representative_frame_id: str | None


#: A human box and a detection are taken to be the same object at this
#: overlap or better.
#:
#: 0.5 is not a round number picked for looking reasonable: it is the
#: threshold detection benchmarks have used for "correct" since PASCAL
#: VOC, and it is what every mAP figure anyone compares this model
#: against means by a match. Using the same number here keeps "the
#: detector found this vehicle" meaning one thing across the project.
#:
#: The failure mode is worth stating. A human who redraws a sloppy
#: detection much tighter can land below 0.5, and their box is then
#: reported as a vehicle the detector missed rather than as a class
#: disagreement. That is the direction to fail in: it sends the case to
#: a human instead of quietly pairing two boxes that may be different
#: objects.
DISAGREEMENT_IOU_THRESHOLD = 0.5

#: The detector saw something there and the human called it something
#: the detector's class does not allow for.
KIND_CLASS_MISMATCH = "class_mismatch"
#: The human drew a box where the detector found nothing.
KIND_MISSED_DETECTION = "missed_detection"


@dataclass
class DisagreementItem:
    """One label worth a second look, and why.

    ``track_id`` is null for a box drawn on the labelling canvas that
    matched no detection - there is no track to send a reviewer to, and
    saying so is what lets the panel offer the frame instead of a button
    that goes nowhere. ``frame_id`` is always present, because every box
    is on a frame however it was written.
    """

    kind: str
    frame_id: str
    annotation_id: str
    human_class_id: int
    human_class_name: str
    #: Null for a missed detection - there is no detector class, which
    #: is the point.
    detector_class: str | None = None
    track_id: str | None = None


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
    """Every human label worth a second look, whichever way it was drawn.

    Two kinds, and the second only exists because canvas labels now
    reach here at all:

    * ``class_mismatch`` - the detector saw something at that box and
      the human's class is not one the detector's class allows for.
    * ``missed_detection`` - the human drew a vehicle the detector did
      not find. There is nothing to disagree with, which is exactly what
      makes it the most useful example in the queue: it is unambiguously
      the model's miss rather than a labelling question.

    How a box finds its detection depends on how it was written. A label
    from track review *is* a detection - it carries the candidate's id -
    so it is matched by that link and never by geometry. A box drawn on
    the canvas has no such link, so it is matched to the best-overlapping
    detection on its own frame, at ``DISAGREEMENT_IOU_THRESHOLD`` or
    better. Guessing is confined to the case that has nothing better.

    Labels on rejected frames are left out. A rejected frame is not going
    to train anything, so queueing its labels would be asking for work
    that changes nothing.
    """
    class_names = class_names_for(db, project_id)
    annotations = list(
        db.scalars(
            project_annotations_for_export(project_id)
            .where(
                Annotation.source == "human",
                Annotation.status.in_(["accepted", "hard"]),
                Annotation.class_id.is_not(None),
            )
            .order_by(Annotation.id)
        )
    )
    if not annotations:
        return []

    candidates_by_frame: dict[str, list[FrameCandidate]] = defaultdict(list)
    frame_ids = sorted({annotation.frame_id for annotation in annotations})
    for chunk in chunked(frame_ids):
        for candidate in db.scalars(select(FrameCandidate).where(FrameCandidate.frame_id.in_(chunk))):
            candidates_by_frame[candidate.frame_id].append(candidate)
    candidates_by_id = {c.id: c for group in candidates_by_frame.values() for c in group}

    disagreements = []
    for annotation in annotations:
        candidate = _detection_for(annotation, candidates_by_frame, candidates_by_id)
        human_class_name = class_names.get(annotation.class_id, str(annotation.class_id))

        if candidate is None:
            disagreements.append(
                DisagreementItem(
                    kind=KIND_MISSED_DETECTION,
                    frame_id=annotation.frame_id,
                    annotation_id=annotation.id,
                    human_class_id=annotation.class_id,
                    human_class_name=human_class_name,
                )
            )
            continue

        plausible = COCO_TO_PLAUSIBLE_ATCC_CLASS_IDS.get(candidate.detector_class)
        if plausible is None:
            continue  # unmapped detector class - no basis to flag anything
        if annotation.class_id not in plausible:
            disagreements.append(
                DisagreementItem(
                    kind=KIND_CLASS_MISMATCH,
                    frame_id=annotation.frame_id,
                    annotation_id=annotation.id,
                    human_class_id=annotation.class_id,
                    human_class_name=human_class_name,
                    detector_class=candidate.detector_class,
                    # Only for a label that was actually made by
                    # reviewing that track. A canvas box matched to a
                    # detection by overlap is not "about" the detection's
                    # track - opening the track would not even show the
                    # box - so the field keeps meaning one thing and the
                    # frame is what a reviewer is sent to instead.
                    track_id=candidate.track_id if annotation.frame_candidate_id is not None else None,
                )
            )
    return disagreements


def _detection_for(
    annotation: Annotation,
    candidates_by_frame: dict[str, list[FrameCandidate]],
    candidates_by_id: dict[str, FrameCandidate],
) -> FrameCandidate | None:
    """The detection this label is about, or ``None`` if there is none.

    A label written through track review names its candidate outright,
    and that link is the truth - re-deriving it from geometry could only
    make it worse. Only a canvas box has to be matched, and then only
    against detections on its own frame.
    """
    if annotation.frame_candidate_id is not None:
        return candidates_by_id.get(annotation.frame_candidate_id)

    on_frame = candidates_by_frame.get(annotation.frame_id, [])
    if not on_frame:
        return None

    box = tuple(annotation.bbox_json)
    best = max(on_frame, key=lambda c: iou(box, tuple(c.bbox_json)))
    return best if iou(box, tuple(best.bbox_json)) >= DISAGREEMENT_IOU_THRESHOLD else None
