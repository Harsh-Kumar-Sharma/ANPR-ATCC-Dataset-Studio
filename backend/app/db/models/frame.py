import uuid
from datetime import datetime, timezone

from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Frame(Base):
    """A full, uncropped frame sampled from a source.

    Phase 2 only ever saved per-detection *crops* (see
    ``track_processor._persist_track``), which is right for review but
    unusable for training a detector: a crop-only dataset teaches the
    model that a vehicle always fills the entire image. Training needs
    the whole frame with every object's box on it.

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
    created_at: Mapped[datetime] = mapped_column(default=_utcnow, nullable=False)
