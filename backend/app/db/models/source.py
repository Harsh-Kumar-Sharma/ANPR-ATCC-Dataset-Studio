import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Source(Base):
    """An imported video source. See docs/05_DATABASE_DESIGN.md."""

    __tablename__ = "sources"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    type: Mapped[str] = mapped_column(String(32), nullable=False, default="video")
    path_or_uri: Mapped[str] = mapped_column(String(1024), nullable=False)
    fps: Mapped[float] = mapped_column(Float, nullable=False)
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    frame_count: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=_utcnow, nullable=False)

    #: A "frozen validation clip" (docs/02_IMPLEMENTATION_PLAN.md Phase 7):
    #: a fixed benchmark source with a manually-counted ground truth,
    #: so detection recall can be measured against a real number
    #: instead of only against the pipeline's own output.
    is_frozen: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    ground_truth_vehicle_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
