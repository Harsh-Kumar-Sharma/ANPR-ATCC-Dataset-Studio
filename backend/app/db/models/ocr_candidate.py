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

    The model's record, and only the model's. A human's plate reading is
    a property of the vehicle in the box and lives on the annotation
    (``services/plate_text.py``); it used to be written here as a
    ``source='human'`` row, which meant the same reading existed in two
    places, in two different forms, with only one of them reaching an
    exported dataset.

    Every attempt is stored and never overwritten - a re-run adds rows.
    ``source`` is therefore always ``model`` on anything written since
    ticket 15; older human rows the migration could not safely move are
    left in place and read by nothing.

    Exactly one row per track has ``selected=True``: the model's own
    best attempt, set by ``run_ocr_for_track`` and written nowhere else.
    It is not a human decision and no longer pretends to be one.
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
