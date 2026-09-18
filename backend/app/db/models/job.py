import uuid
from datetime import datetime, timezone

from sqlalchemy import Float, ForeignKey, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


#: Detection blocked an HTTP request for one to three minutes; training
#: will take hours. Both need the same machinery, so it is built once
#: and proven on detection first. Export and pre-annotation join later.
JOB_TYPES = ("detect", "train", "export", "preannotate")

#: ``pending`` means submitted and launched but not yet picked up by its
#: worker - a real state, not a placeholder, because process start is
#: slow enough to be visible. Terminal states are the last three.
JOB_STATUSES = ("pending", "running", "succeeded", "failed", "cancelled")

TERMINAL_JOB_STATUSES = frozenset({"succeeded", "failed", "cancelled"})


class Job(Base):
    """A unit of work running outside the request that asked for it.

    The row is the app's view of a detached OS process. ``pid`` is what
    makes that view recoverable: the app can be closed and reopened and
    still find the process it started (see the reattach behaviour that
    training depends on).

    Live progress does *not* live here - it is in a file owned by the
    worker, see ``app.services.jobs.progress``. This row carries the
    authoritative lifecycle and the final outcome.
    """

    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    # Nullable so a future maintenance job that belongs to no single
    # project still fits. Every job type today has one.
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id"), nullable=True, index=True)
    type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending", index=True)

    #: Everything the worker needs to do the job, so it can start from
    #: nothing but its own id - it does not share memory with the app.
    params_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    #: What the job produced (e.g. the processing run id), for the UI to
    #: link to once the job finishes.
    result_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    #: Last known progress, mirrored from the progress file when the job
    #: reaches a terminal state, so a finished job still reads sensibly
    #: after its progress file is gone.
    progress: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    progress_message: Mapped[str | None] = mapped_column(String(512), nullable=True)

    pid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(2048), nullable=True)

    created_at: Mapped[datetime] = mapped_column(default=_utcnow, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_JOB_STATUSES
