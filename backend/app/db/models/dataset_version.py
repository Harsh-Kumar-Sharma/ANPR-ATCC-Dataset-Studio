import uuid
from datetime import datetime, timezone

from sqlalchemy import ForeignKey, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class DatasetVersion(Base):
    """An exported, versioned dataset snapshot. See docs/05_DATABASE_DESIGN.md.

    Immutable after creation - docs/05_DATABASE_DESIGN.md: "Dataset
    versions are immutable after finalization." A re-export always
    creates a new version rather than mutating this one.
    """

    __tablename__ = "dataset_versions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=_utcnow, nullable=False)
    split_seed: Mapped[int] = mapped_column(Integer, nullable=False)
    config_snapshot_json: Mapped[dict] = mapped_column(JSON, nullable=False)
