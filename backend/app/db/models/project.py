import uuid
from datetime import datetime, timezone

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Project(Base):
    """A dataset studio project. See docs/05_DATABASE_DESIGN.md."""

    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=_utcnow, nullable=False)
    class_schema_version: Mapped[str] = mapped_column(String(32), nullable=False, default="v1")
    workspace_path: Mapped[str] = mapped_column(String(1024), nullable=False)
