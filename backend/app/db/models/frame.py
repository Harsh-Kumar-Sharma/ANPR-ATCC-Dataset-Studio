import uuid
from datetime import datetime, timezone

from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


#: A frame's place in the labelling queue.
#:
#: ``rejected`` is a human saying "I looked, not worth labelling".
#: ``skipped`` is frame selection never offering it in the first
#: place. Both leave the queue and both can be undone, but they are
#: different judgements and the progress line counts them apart.
FRAME_STATUSES = ("pending", "labeled", "rejected", "skipped")

#: Statuses that keep a frame out of the queue.
SET_ASIDE_STATUSES = ("rejected", "skipped")


class Frame(Base):
    """A full, uncropped frame sampled from a source.

    Phase 2 only ever saved per-detection *crops* (see
    ``track_processor._persist_track``), which is right for review but
    unusable for training a detector: a crop-only dataset teaches the
    model that a vehicle always fills the entire image. Training needs
    the whole frame with every object's box on it.

    A frame is also the unit of labelling: its annotations hang off it
    directly, many per frame. See ``Annotation``.

    ``image_path`` is deliberately nullable. For a video source, the
    source file is copied into the project workspace at import and never
    deleted, and ``frame_index`` identifies the frame exactly - so the
    pixels are always *recoverable* by re-decoding, and storing every
    sampled frame up front would cost hundreds of MB per clip for frames
    that may never be labeled. A null path therefore means "not
    materialized yet, decode it on demand" (see
    ``services/frame_materializer.py``), not "lost".
    """

    __tablename__ = "frames"
    __table_args__ = (UniqueConstraint("source_id", "frame_index", name="uq_frames_source_frame_index"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"), nullable=False, index=True)
    frame_index: Mapped[int] = mapped_column(Integer, nullable=False)
    timestamp_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    image_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    #: Where this frame is in the labelling queue. ``pending`` until a
    #: human has either saved its boxes (``labeled``, which includes
    #: "there is nothing here") or thrown it out (``rejected``), or until
    #: frame selection passed over it (``skipped``).
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", index=True)
    #: Why this frame entered the queue. Debuggability: a queue full of
    #: near-duplicates is only fixable if each row can say how it got in.
    selection_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=_utcnow, nullable=False)
