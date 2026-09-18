import uuid
from datetime import datetime, timezone

from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ClassDefinition(Base):
    """One class a project can label with.

    Classes used to be a hardcoded module-level list shared by every
    project, versioned only by a string on the project row. That made an
    ANPR project carry twenty traffic-survey categories it would never
    use, and made editing the list a code change.

    They are now rows a project owns, seeded by copying a preset (see
    ``app.core.presets``) so no two projects share a live list.
    """

    __tablename__ = "class_definitions"
    __table_args__ = (
        # A label points at a class_id, so it must mean exactly one thing
        # within a project.
        UniqueConstraint("project_id", "class_id", name="uq_class_definitions_project_class_id"),
        # Two classes with the same name are indistinguishable to whoever
        # is labelling, which is how a dataset ends up split across two
        # categories that were meant to be one.
        UniqueConstraint("project_id", "name", name="uq_class_definitions_project_name"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)

    #: Stable identifier that annotations refer to. Deliberately *not* the
    #: YOLO index: export numbers classes contiguously at export time, so
    #: deleting a class does not renumber the labels of every other one.
    class_id: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)

    #: The order the user sees, and the order export assigns indices in.
    #: Separate from class_id so the list can be rearranged without
    #: touching a single label.
    display_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    created_at: Mapped[datetime] = mapped_column(default=_utcnow, nullable=False)
