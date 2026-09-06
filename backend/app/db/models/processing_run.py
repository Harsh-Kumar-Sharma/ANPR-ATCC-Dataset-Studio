import uuid
from datetime import datetime, timezone

from sqlalchemy import ForeignKey, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ProcessingRun(Base):
    """A processing run against a source. See docs/05_DATABASE_DESIGN.md.

    Phase 1 only performs frame sampling, so ``detector_version`` and
    ``tracker_config`` stay null until Phase 2 wires in detection and
    tracking.
    """

    __tablename__ = "processing_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"), nullable=False, index=True)
    detector_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tracker_config: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    sampling_config: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    sampled_frame_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    started_at: Mapped[datetime] = mapped_column(default=_utcnow, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)
