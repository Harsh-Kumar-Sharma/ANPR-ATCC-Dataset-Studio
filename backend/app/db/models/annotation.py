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
    """A class/bbox judgment on a frame candidate. See docs/05_DATABASE_DESIGN.md.

    ``source`` distinguishes ``model`` (auto-suggested - not populated
    yet, since there is no COCO -> ATCC class mapping model) from
    ``human`` (the reviewer's truth, which always wins per decision
    D-003 in docs/DECISIONS.md). A track has at most one active human
    annotation - editing which frame/class/bbox is chosen updates that
    same row rather than creating duplicates.
    """

    __tablename__ = "annotations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    frame_candidate_id: Mapped[str] = mapped_column(ForeignKey("frame_candidates.id"), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    class_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bbox_json: Mapped[list] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    updated_at: Mapped[datetime] = mapped_column(default=_utcnow, onupdate=_utcnow, nullable=False)
