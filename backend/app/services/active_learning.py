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
#: 0.3, matching ``DEFAULT_FRAGMENTATION_IOU_THRESHOLD`` in
#: ``track_metrics``, because that answers the same question this one
#: does: are these two boxes the same vehicle? It is deliberately not
#: the 0.5 that detection benchmarks use for "is this detection correct"
#: - scoring a detection and associating one with a human's box are
#: different questions, and the first is the stricter of the two. An
#: earlier version used 0.5 and reported a human who tightened a loose
#: box to 70% of each side as a vehicle the detector had missed, which
#: is an ordinary redraw and a false accusation against the model.
#:
#: Uncalibrated, like every other threshold here - a reasonable starting
#: point, not tuned against real footage.
DISAGREEMENT_IOU_THRESHOLD = 0.3

#: The detector matched this box and the human called it something the
#: detector's class does not allow for.
KIND_CLASS_MISMATCH = "class_mismatch"
#: Nothing the detector found matches this box. Often a vehicle it
#: missed, which is the most useful case in the queue - but the entry
#: claims only what is known, because a box drawn far enough from the
#: detection it belongs to lands here too.
KIND_UNMATCHED_BOX = "unmatched_box"


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


def find_model_human_disagreements(db: Session, project_id: str, limit: int = 50) -> list[DisagreementItem]:
    """Every human label worth a second look, whichever way it was drawn.

    Two kinds, and the second only exists because canvas labels now
    reach here at all:

    * ``class_mismatch`` - detections match that box, and none of them
      allows for the class the human chose.
    * ``unmatched_box`` - nothing the detector found matches that box.
      Often a vehicle it missed, which is the most useful case here; but
      the entry claims only what is known, because a box drawn far
      enough from the detection it belongs to lands here too.

    How a box finds its detection depends on how it was written. A label
    from track review *is* a detection - it carries the candidate's id -
    so it is matched by that link and never by geometry. A box drawn on
    the canvas has no such link, so it is matched against the detections
    on its own frame at ``DISAGREEMENT_IOU_THRESHOLD`` or better.

    *Every* matching detection is asked, not the best-overlapping one.
    Two vehicles nearly on top of each other is the ordinary dense case
    on gantry footage, and picking the single highest-overlap detection
    flagged a correct label as wrong whenever the other one was the one
    the human meant - with the tie broken by database row order, so the
    answer was not even stable. If any matching detection allows for the
    human's class, there is nothing to report.

    Labels on rejected frames are left out. A rejected frame is not going
    to train anything, so queueing its labels would be asking for work
    that changes nothing.

    ``limit`` caps the result, as it does on the two queues either side
    of this one. Every canvas box the detector missed is an entry, which
    on real footage is thousands, and the panel renders them in one list.
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
        # Ordered so the answer cannot depend on row order - the same
        # determinism the export query goes out of its way to keep.
        for candidate in db.scalars(
            select(FrameCandidate).where(FrameCandidate.frame_id.in_(chunk)).order_by(FrameCandidate.id)
        ):
            candidates_by_frame[candidate.frame_id].append(candidate)
    candidates_by_id = {c.id: c for group in candidates_by_frame.values() for c in group}

    disagreements: list[DisagreementItem] = []
    for annotation in annotations:
        matches = _detections_for(annotation, candidates_by_frame, candidates_by_id)
        if matches is None:
            continue  # nothing can be said about this one; see _detections_for
        human_class_name = class_names.get(annotation.class_id, str(annotation.class_id))

        if not matches:
            disagreements.append(
                DisagreementItem(
                    kind=KIND_UNMATCHED_BOX,
                    frame_id=annotation.frame_id,
                    annotation_id=annotation.id,
                    human_class_id=annotation.class_id,
                    human_class_name=human_class_name,
                )
            )
        elif not _any_allows(annotation.class_id, matches):
            # Named after the first matching detection that has an
            # opinion, so the message quotes a class the detector
            # actually gave rather than an arbitrary one.
            named = next(c for c in matches if c.detector_class in COCO_TO_PLAUSIBLE_ATCC_CLASS_IDS)
            disagreements.append(
                DisagreementItem(
                    kind=KIND_CLASS_MISMATCH,
                    frame_id=annotation.frame_id,
                    annotation_id=annotation.id,
                    human_class_id=annotation.class_id,
                    human_class_name=human_class_name,
                    detector_class=named.detector_class,
                    # Only for a label that was actually made by
                    # reviewing that track. A canvas box matched to a
                    # detection by overlap is not "about" the detection's
                    # track - opening the track would not even show the
                    # box - so the field keeps meaning one thing and the
                    # frame is what a reviewer is sent to instead.
                    track_id=named.track_id if annotation.frame_candidate_id is not None else None,
                )
            )

        if len(disagreements) >= limit:
            break

    return disagreements


def _any_allows(class_id: int, matches: list[FrameCandidate]) -> bool:
    """Does any matching detection allow for the class the human chose?

    A detector class nobody has mapped gives no basis to flag anything,
    so it allows everything - the same "no opinion" the earlier
    single-detection version expressed by skipping.
    """
    for candidate in matches:
        plausible = COCO_TO_PLAUSIBLE_ATCC_CLASS_IDS.get(candidate.detector_class)
        if plausible is None or class_id in plausible:
            return True
    return False


def _detections_for(
    annotation: Annotation,
    candidates_by_frame: dict[str, list[FrameCandidate]],
    candidates_by_id: dict[str, FrameCandidate],
) -> list[FrameCandidate] | None:
    """Every detection this label could be about.

    ``None`` means "nothing can be said", which is not the same as an
    empty list. A label written through track review names its candidate
    outright, and that link is the truth - re-deriving it from geometry
    could only make it worse. If that candidate cannot be loaded, the
    gap is in the data rather than in the model, and answering "the
    detector missed this" would be the exact opposite of the truth.

    Only a canvas box is matched geometrically, and then against every
    detection on its own frame rather than only the best-overlapping
    one.
    """
    if annotation.frame_candidate_id is not None:
        named = candidates_by_id.get(annotation.frame_candidate_id)
        return [named] if named is not None else None

    box = tuple(annotation.bbox_json)
    return [
        candidate
        for candidate in candidates_by_frame.get(annotation.frame_id, [])
        if iou(box, tuple(candidate.bbox_json)) >= DISAGREEMENT_IOU_THRESHOLD
    ]
