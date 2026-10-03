import uuid
from datetime import datetime, timezone

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


#: Where a task is. Moves forward on its own when the assignee starts,
#: and by hand for the rest:
#:
#:   assigned -> in_progress   the assignee opened one of its frames
#:   in_progress -> in_review  the assignee submitted, nothing pending
#:   in_review -> done         an admin accepted it
#:   in_review -> in_progress  an admin rejected it, with a reason
#:   done -> in_progress       an admin reopened it
TASK_ASSIGNED = "assigned"
TASK_IN_PROGRESS = "in_progress"
TASK_IN_REVIEW = "in_review"
TASK_DONE = "done"
TASK_STATUSES = (TASK_ASSIGNED, TASK_IN_PROGRESS, TASK_IN_REVIEW, TASK_DONE)


class LabelingTask(Base):
    """Labelling one source's frames, given to one person.

    One task per source: the frames that make up the task are simply
    the source's frames, so a live capture that keeps adding frames
    keeps adding them to the task, and nothing about a frame records
    which task it is in.
    """

    __tablename__ = "labeling_tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"), nullable=False, unique=True)
    #: Null once the person it was given to has been deleted.
    assignee_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=TASK_ASSIGNED)
    #: Why an admin sent it back. Cleared when it is submitted again.
    review_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=_utcnow, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)
