"""The labelling queue, and the boxes on a frame.

The frame is the unit of labelling. Everything here takes that as
given: a frame has a place in the queue, an image that can be fetched,
and a set of boxes that is replaced whole on every save.

Replacing the whole set is what makes "delete a box" work without a
delete endpoint - and it is also why a save is all-or-nothing. Every
box is validated before any existing one is touched, so one bad box
cannot wipe the rest.
"""

import math
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.errors import AppError, ConflictError, NotFoundError
from app.db.models.annotation import Annotation
from app.db.models.frame import FRAME_STATUSES, SET_ASIDE_STATUSES, Frame
from app.db.models.project import Project
from app.db.models.source import Source
from app.services.annotation_attributes import InvalidAttributeError, clean_attributes
from app.services.annotations import delete_annotations
from app.services.class_definitions import is_valid_class_id
from app.services.frame_materializer import materialize_frames


class InvalidBoxError(AppError):
    """A box that could not be on this frame, or a class this project does
    not have. Coded so the canvas can say which box and why."""

    code = "invalid_box"


class StaleBoxError(ConflictError):
    """The canvas echoed an annotation that is no longer on the frame.

    Its view is out of date - something else removed the box - and the
    honest answer is to reload rather than to guess whether the user
    meant to keep it."""

    code = "stale_box"


class UnknownFrameStatusError(AppError):
    """A frame status that does not exist, asked for as a filter or set
    on a frame. Rejected rather than quietly returning an empty queue,
    which reads like "nothing left to label"."""

    code = "unknown_frame_status"


@dataclass(frozen=True)
class BoxInput:
    """One box as the canvas sends it: ``[x1, y1, x2, y2]`` in full-frame
    pixels, a class (or none yet), and whatever attributes it carries.

    ``id`` is the existing annotation this box is, when it is one the
    canvas loaded rather than drew. Echoing it lets an unchanged box
    stay itself - same row, same id, same dataset items indexing it.
    """

    class_id: int | None
    bbox: list[float]
    attributes: dict = field(default_factory=dict)
    id: str | None = None


def _require_known_status(status: str) -> None:
    if status not in FRAME_STATUSES:
        raise UnknownFrameStatusError(
            f"Unknown frame status: {status!r}. Expected one of {', '.join(FRAME_STATUSES)}."
        )


def _openable_frames(project_id: str):
    """Every frame in the project the queue could ever offer.

    "Could ever" excludes a frame from a live RTSP session that has no
    stored image: there is no video file to decode it from, so it would
    be a queue entry that errors on click. Written once because the list
    and the progress counts have to agree - if they drift, the totals
    stop describing the list.
    """
    return (
        select(Frame)
        .join(Source, Frame.source_id == Source.id)
        .where(Source.project_id == project_id)
        .where(or_(Source.type == "video", Frame.image_path.is_not(None)))
    )


def list_queue(db: Session, project_id: str, status: str | None = None) -> list[Frame]:
    """A project's frames in labelling order.

    Source then frame index, so a session walks one video forward before
    starting the next. Which frames *deserve* to be in the queue is a
    later ticket; today every captured frame that can be opened is.

    "Can be opened" matters: a frame from a live RTSP session has no
    video file to decode it from, so unless its image was written at
    capture time there is nothing to show. Offering it would be a queue
    entry that errors on click.
    """
    if status is not None:
        _require_known_status(status)

    stmt = _openable_frames(project_id).order_by(Frame.source_id, Frame.frame_index)
    if status is not None:
        stmt = stmt.where(Frame.status == status)
    else:
        # A frame a human rejected, or that selection passed over, is not
        # offered again - but both stay reachable by asking for that
        # status, which is how either judgement gets undone.
        stmt = stmt.where(Frame.status.not_in(SET_ASIDE_STATUSES))
    return list(db.scalars(stmt))


def set_status(db: Session, frame: Frame, status: str) -> Frame:
    """Move a frame to a place in the queue by hand.

    Rejecting says "not worth labelling", not "destroy my work": the
    boxes already on the frame stay exactly where they are, because the
    judgement can be reversed by putting the frame back.

    Putting it back asks for ``pending``, but what it gets is whatever
    is true of the frame - a frame that was labelled before it was set
    aside is still labelled, and saying otherwise would both misreport
    it and leave the progress counts wrong.
    """
    _require_known_status(status)
    frame.status = status if status in SET_ASIDE_STATUSES else _status_from_work(db, frame)
    db.flush()
    return frame


def _status_from_work(db: Session, frame: Frame) -> str:
    """What this frame's status should be, judged by what is on it."""
    return "labeled" if any(a.source == "human" for a in list_annotations(db, frame.id)) else "pending"


def queue_progress(db: Session, project_id: str) -> dict[str, int]:
    """How far through this project's frames the labelling has got.

    Counts every frame the queue would ever offer, rejected included -
    "12 of 400, 3 skipped" is the shape of the answer, so the skipped
    ones have to be in the total.
    """
    openable = _openable_frames(project_id).subquery()
    rows = db.execute(select(openable.c.status, func.count()).select_from(openable).group_by(openable.c.status)).all()
    counts = {status: 0 for status in FRAME_STATUSES}
    for status, count in rows:
        counts[status] = count
    return {**counts, "total": sum(counts.values())}


def get_frame(db: Session, frame_id: str) -> Frame:
    frame = db.get(Frame, frame_id)
    if frame is None:
        raise NotFoundError(f"Frame not found: {frame_id}")
    return frame


def project_of(db: Session, frame: Frame) -> Project:
    """The project a frame belongs to, via its source."""
    source = db.get(Source, frame.source_id)
    project = db.get(Project, source.project_id) if source is not None else None
    if project is None:
        raise NotFoundError(f"Frame {frame.id} has no project")
    return project


def frame_image_path(db: Session, frame: Frame) -> Path:
    """Where this frame's image is on disk, decoding it from the source
    video if it has never been needed before.

    Frames are recorded cheaply at detection time and only materialised
    when something wants the pixels - see ``Frame.image_path``. The
    decoded file is remembered on the row, so the second request is a
    stat, not a decode.
    """
    source = db.get(Source, frame.source_id)
    project = project_of(db, frame)
    return materialize_frames(db, [frame], source, Path(project.workspace_path))[frame.id]


def list_annotations(db: Session, frame_id: str) -> list[Annotation]:
    """Every box on a frame, oldest first."""
    return list(
        db.scalars(
            select(Annotation).where(Annotation.frame_id == frame_id).order_by(Annotation.updated_at, Annotation.id)
        )
    )


def replace_annotations(db: Session, project_id: str, frame: Frame, boxes: list[BoxInput]) -> list[Annotation]:
    """Make ``boxes`` the complete set of boxes on this frame.

    Whole-set replacement is the contract: a box the canvas no longer
    sends is gone, including one that was written the old way through
    track review - the canvas is the truth for its frame. Whatever
    depended on a removed box is settled by ``delete_annotations``.

    But "replacement" is of the *set*, not of the rows. A box the canvas
    loaded comes back with its id and is updated in place, so an
    unchanged box keeps its identity, the dataset items indexing it, and
    its candidate link. Deleting and re-inserting everything would have
    silently dropped an exported box's dataset item and un-reviewed its
    track on every save.

    "Every box" includes a model's. A prediction the canvas sends back is
    one the human looked at and kept - it becomes the human's box. One
    the canvas leaves out was rejected and goes. That is the whole
    pre-annotation loop, and it needs nothing more than this. A box the
    human already reviewed keeps the decision it was given: editing its
    geometry or class is not a re-review.

    Zero boxes is a real label. An empty frame teaches "nothing here",
    and the frame reads as labelled, not as still waiting.

    Validation runs over every box before anything is touched, so a
    single bad box leaves the previous set exactly as it was.
    """
    existing = {a.id: a for a in list_annotations(db, frame.id)}

    # Validated - and cleaned - before anything is touched, so one bad
    # box leaves the previous set exactly as it was, attributes included.
    # What the box already holds goes in too: it is what tells an echo of
    # a retired attribute apart from a client inventing one.
    cleaned = [
        _validate(db, project_id, frame, index, box, stored_attributes(existing.get(box.id)))
        for index, box in enumerate(boxes)
    ]

    echoed = [box.id for box in boxes if box.id is not None]
    if len(echoed) != len(set(echoed)):
        raise InvalidBoxError("The same annotation was sent more than once.")
    unknown = [annotation_id for annotation_id in echoed if annotation_id not in existing]
    if unknown:
        raise StaleBoxError(
            f"{len(unknown)} box(es) refer to annotations no longer on this frame. Reload it and try again."
        )

    for box, attributes in zip(boxes, cleaned, strict=True):
        if box.id is not None:
            annotation = existing[box.id]
            annotation.class_id = box.class_id
            annotation.bbox_json = [float(v) for v in box.bbox]
            annotation.attributes = attributes
            if annotation.source != "human":
                # Sending a prediction back is confirming it.
                annotation.source = "human"
                annotation.status = "accepted"
        else:
            db.add(
                Annotation(
                    frame_id=frame.id,
                    frame_candidate_id=None,
                    source="human",
                    class_id=box.class_id,
                    bbox_json=[float(v) for v in box.bbox],
                    attributes=attributes,
                    # A box a human drew is the human's truth for that frame.
                    status="accepted",
                )
            )

    kept = set(echoed)
    delete_annotations(db, [annotation_id for annotation_id in existing if annotation_id not in kept])

    frame.status = "labeled"
    db.flush()
    return list_annotations(db, frame.id)


def stored_attributes(annotation: Annotation | None) -> dict:
    """What this box already holds, or nothing if it is a new one."""
    return dict(annotation.attributes or {}) if annotation is not None else {}


def _validate(
    db: Session, project_id: str, frame: Frame, index: int, box: BoxInput, stored: dict | None = None
) -> dict:
    """Check one box, and return the attributes that should be stored
    for it. Returning rather than mutating keeps the whole check-first
    pass free of side effects.

    ``index`` is zero-based here and one-based in every message, because
    the canvas numbers boxes from one on screen ("Box 1 of 3") and two
    numbering schemes for the same box costs a user real time.
    """
    if len(box.bbox) != 4 or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in box.bbox):
        raise InvalidBoxError(f"Box {index + 1}: expected four finite numbers [x1, y1, x2, y2].")

    x1, y1, x2, y2 = box.bbox
    if not (0 <= x1 < x2 <= frame.width and 0 <= y1 < y2 <= frame.height):
        raise InvalidBoxError(
            f"Box {index + 1}: [{x1}, {y1}, {x2}, {y2}] is not inside this {frame.width}x{frame.height} frame, "
            "or has no area."
        )

    if box.class_id is not None and not is_valid_class_id(db, project_id, box.class_id):
        raise InvalidBoxError(f"Box {index + 1}: class {box.class_id} is not one of this project's classes.")

    try:
        return clean_attributes(box.attributes, stored=stored)
    except InvalidAttributeError as e:
        raise InvalidBoxError(f"Box {index + 1}: {e}") from e
