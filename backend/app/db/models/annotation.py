import uuid
from datetime import datetime, timezone

from sqlalchemy import ForeignKey, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Annotation(Base):
    """One box on one frame.

    The frame is the unit of labelling. A frame chosen because it is the
    best shot of one vehicle still has every other vehicle in it, and
    those must be labelled too - unlabelled objects teach a detector
    "nothing here". So a frame holds many annotations, and nothing about
    this row limits how many.

    Annotations used to hang off a frame *candidate* - a per-detection
    crop - with the review service upserting one per track. Both facts
    made a second box on a frame structurally impossible. The candidate
    link survives as ``frame_candidate_id`` for labels written by the
    track-review flow; labels drawn on the canvas have none.

    ``source`` distinguishes ``model`` (a prediction, arriving as a
    pre-annotation the human corrects) from ``human`` (the reviewer's
    truth, which always wins). ``attributes`` is one generic field for
    plate text, colour, direction, occlusion and whatever comes next, so
    a new attribute never needs a migration.
    """

    __tablename__ = "annotations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    frame_id: Mapped[str] = mapped_column(ForeignKey("frames.id"), nullable=False, index=True)
    #: Set only by the legacy track-review path. Nullable because a box
    #: drawn on the canvas belongs to the frame, not to any detection.
    frame_candidate_id: Mapped[str | None] = mapped_column(
        ForeignKey("frame_candidates.id"), nullable=True, index=True
    )
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    class_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: ``[x1, y1, x2, y2]`` in full-frame pixels.
    bbox_json: Mapped[list] = mapped_column(JSON, nullable=False)
    attributes: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    updated_at: Mapped[datetime] = mapped_column(default=_utcnow, onupdate=_utcnow, nullable=False)
