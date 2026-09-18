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

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError, NotFoundError
from app.db.models.annotation import Annotation
from app.db.models.frame import FRAME_STATUSES, Frame
from app.db.models.project import Project
from app.db.models.source import Source
from app.services.annotations import delete_annotations
from app.services.class_definitions import is_valid_class_id
from app.services.frame_materializer import materialize_frames


class InvalidBoxError(AppError):
    """A box that could not be on this frame, or a class this project does
    not have. Coded so the canvas can say which box and why."""

    code = "invalid_box"


@dataclass(frozen=True)
class BoxInput:
    """One box as the canvas sends it: ``[x1, y1, x2, y2]`` in full-frame
    pixels, a class (or none yet), and whatever attributes it carries."""

    class_id: int | None
    bbox: list[float]
    attributes: dict = field(default_factory=dict)


def list_queue(db: Session, project_id: str, status: str | None = None) -> list[Frame]:
    """A project's frames in labelling order.

    Source then frame index, so a session walks one video forward before
    starting the next. Which frames *deserve* to be in the queue is a
    later ticket; today every captured frame is.
    """
    if status is not None and status not in FRAME_STATUSES:
        raise NotFoundError(f"Unknown frame status: {status!r}. Expected one of {', '.join(FRAME_STATUSES)}.")

    stmt = (
        select(Frame)
        .join(Source, Frame.source_id == Source.id)
        .where(Source.project_id == project_id)
        .order_by(Frame.source_id, Frame.frame_index)
    )
    if status is not None:
        stmt = stmt.where(Frame.status == status)
    return list(db.scalars(stmt))


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
    """Make ``boxes`` the complete set of human boxes on this frame.

    Whole-set replacement is the contract: a box the canvas no longer
    sends is gone, including one that was written the old way through
    track review - the canvas is the truth for its frame. Whatever
    depended on a removed box is settled by ``delete_annotations``.

    Zero boxes is a real label. An empty frame teaches "nothing here",
    and the frame reads as labelled, not as still waiting.

    Validation runs over every box before anything is touched, so a
    single bad box leaves the previous set exactly as it was.
    """
    for index, box in enumerate(boxes):
        _validate(db, project_id, frame, index, box)

    existing_human = [a.id for a in list_annotations(db, frame.id) if a.source == "human"]
    delete_annotations(db, existing_human)

    db.add_all(
        Annotation(
            frame_id=frame.id,
            frame_candidate_id=None,
            source="human",
            class_id=box.class_id,
            bbox_json=[float(v) for v in box.bbox],
            attributes=dict(box.attributes),
            # A box a human drew is the human's truth for that frame.
            status="accepted",
        )
        for box in boxes
    )
    frame.status = "labeled"
    db.flush()
    return list_annotations(db, frame.id)


def _validate(db: Session, project_id: str, frame: Frame, index: int, box: BoxInput) -> None:
    if len(box.bbox) != 4 or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in box.bbox):
        raise InvalidBoxError(f"Box {index}: expected four finite numbers [x1, y1, x2, y2].")

    x1, y1, x2, y2 = box.bbox
    if not (0 <= x1 < x2 <= frame.width and 0 <= y1 < y2 <= frame.height):
        raise InvalidBoxError(
            f"Box {index}: [{x1}, {y1}, {x2}, {y2}] is not inside this {frame.width}x{frame.height} frame, "
            "or has no area."
        )

    if box.class_id is not None and not is_valid_class_id(db, project_id, box.class_id):
        raise InvalidBoxError(f"Box {index}: class {box.class_id} is not one of this project's classes.")
