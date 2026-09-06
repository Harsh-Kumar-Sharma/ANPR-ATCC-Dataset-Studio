import uuid
from datetime import datetime, timezone

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Track(Base):
    """A vehicle track. See docs/05_DATABASE_DESIGN.md.

    ``bucket`` (BEST_DETECTION/HARD/FAILED) is set by the Phase 3
    ranking logic and stays null until then. ``review_status`` starts
    at ``unreviewed`` ahead of the Phase 4 review UI.
    """

    __tablename__ = "tracks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(ForeignKey("processing_runs.id"), nullable=False, index=True)
    tracker_track_id: Mapped[int] = mapped_column(Integer, nullable=False)
    start_ts: Mapped[int] = mapped_column(Integer, nullable=False)
    end_ts: Mapped[int] = mapped_column(Integer, nullable=False)
    bucket: Mapped[str | None] = mapped_column(String(32), nullable=True)
    review_status: Mapped[str] = mapped_column(String(32), nullable=False, default="unreviewed")
    created_at: Mapped[datetime] = mapped_column(default=_utcnow, nullable=False)
