import uuid
from datetime import datetime, timezone

from sqlalchemy import Float, ForeignKey, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class FrameCandidate(Base):
    """A candidate frame within a vehicle track. See docs/05_DATABASE_DESIGN.md.

    ``blur_score``/``sharpness_score``/``area_ratio``/``flags_json``
    are populated by the Phase 3 frame-quality signals and stay null
    until then - Phase 2 only records what the detector/tracker
    directly observed.
    """

    __tablename__ = "frame_candidates"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    track_id: Mapped[str] = mapped_column(ForeignKey("tracks.id"), nullable=False, index=True)
    # The full frame this crop was taken from (Phase 10). Nullable
    # because rows created before frames existed have no parent row -
    # they can still be matched by (source, frame_index) if needed.
    frame_id: Mapped[str | None] = mapped_column(ForeignKey("frames.id"), nullable=True, index=True)
    frame_index: Mapped[int] = mapped_column(Integer, nullable=False)
    timestamp_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    #: Null for an offline run: the crop is cut out of the source
    #: video on demand instead of being written during detection. Set
    #: for a live capture, whose pixels cannot be recovered.
    image_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    bbox_json: Mapped[list] = mapped_column(JSON, nullable=False)
    detector_class: Mapped[str] = mapped_column(String(64), nullable=False)
    detector_confidence: Mapped[float] = mapped_column(Float, nullable=False)
    blur_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    sharpness_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    area_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    flags_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=_utcnow, nullable=False)
