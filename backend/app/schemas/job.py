from datetime import datetime

from pydantic import BaseModel, ConfigDict


class JobRead(BaseModel):
    """A job as the UI sees it.

    ``progress`` is merged from the worker's progress file while the job
    runs and from the row once it is finished, so a caller never has to
    know which of the two is authoritative right now.
    """

    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str | None
    type: str
    status: str
    progress: float
    progress_message: str | None
    result_json: dict | None
    error_message: str | None
    pid: int | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


class JobSubmitted(BaseModel):
    """The response to submitting work that now runs in the background.

    Carries the processing run as well as the job so the UI can link to
    the run immediately, rather than waiting for a worker to start.
    ``run_id`` is null for work that produces no run of its own, such as
    frame selection.
    """

    job: JobRead
    run_id: str | None = None


class ClearFinishedJobsRequest(BaseModel):
    """Which project's finished jobs to forget."""

    project_id: str


class JobsClearedRead(BaseModel):
    """How many job rows were forgotten."""

    removed: int
