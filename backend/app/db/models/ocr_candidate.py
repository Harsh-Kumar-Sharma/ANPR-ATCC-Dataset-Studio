import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, Float, ForeignKey, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class OcrCandidate(Base):
    """One OCR attempt on a frame candidate. See docs/05_DATABASE_DESIGN.md.

    Every attempt is stored (traceable), never overwritten - a re-run
    or a human correction adds a new row. ``source`` mirrors
    ``Annotation.source`` (D-003: human truth is separate from and
    overrides model output). Exactly one row per track has
    ``selected=True`` at a time - the track-level chosen OCR result.
    """

    __tablename__ = "ocr_candidates"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    track_id: Mapped[str] = mapped_column(ForeignKey("tracks.id"), nullable=False, index=True)
    frame_candidate_id: Mapped[str] = mapped_column(ForeignKey("frame_candidates.id"), nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="model")
    plate_bbox_json: Mapped[list] = mapped_column(JSON, nullable=False)
    text: Mapped[str] = mapped_column(String(64), nullable=False)
    normalized_text: Mapped[str] = mapped_column(String(64), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    selected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(default=_utcnow, nullable=False)
