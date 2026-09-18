"""Reading background jobs.

Jobs are submitted by the endpoints that need work done (see
``sources.py``), not here. This router is the read side: what is
running, how far along, and what happened.
"""

import asyncio
import logging

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError, NotFoundError
from app.db.models.job import JOB_STATUSES, JOB_TYPES, Job
from app.db.session import SessionLocal, get_db
from app.schemas.job import JobRead
from app.services.jobs import runner

logger = logging.getLogger(__name__)


class ValidationError(AppError):
    code = "invalid_filter"


router = APIRouter(prefix="/jobs", tags=["jobs"])

#: Fast enough that a progress bar looks live, slow enough that polling a
#: small JSON file costs nothing.
_STREAM_POLL_SECONDS = 0.5

#: A finished job still gets one final event, so a client that connects
#: late sees the outcome instead of an empty stream.
_DEFAULT_JOB_LIMIT = 50


def _to_read_model(job: Job) -> JobRead:
    """Merge live progress over the persisted row."""
    fraction, message = runner.current_progress(job)
    model = JobRead.model_validate(job)
    return model.model_copy(update={"progress": fraction, "progress_message": message})


def get_job_or_404(db: Session, job_id: str) -> Job:
    job = db.get(Job, job_id)
    if job is None:
        raise NotFoundError(f"Job not found: {job_id}")
    return job


@router.get("", response_model=list[JobRead])
def list_jobs(
    project_id: str | None = None,
    status: str | None = None,
    type: str | None = None,
    limit: int = Query(_DEFAULT_JOB_LIMIT, ge=1, le=500),
    db: Session = Depends(get_db),
) -> list[JobRead]:
    # Reject unknown filter values rather than silently returning an
    # empty list, which reads identically to "nothing has run yet".
    if status is not None and status not in JOB_STATUSES:
        raise ValidationError(f"Unknown job status: {status!r}. Expected one of {', '.join(JOB_STATUSES)}.")
    if type is not None and type not in JOB_TYPES:
        raise ValidationError(f"Unknown job type: {type!r}. Expected one of {', '.join(JOB_TYPES)}.")

    stmt = select(Job).order_by(Job.created_at.desc()).limit(limit)
    if project_id is not None:
        stmt = stmt.where(Job.project_id == project_id)
    if status is not None:
        stmt = stmt.where(Job.status == status)
    if type is not None:
        stmt = stmt.where(Job.type == type)
    return [_to_read_model(job) for job in db.scalars(stmt)]


@router.get("/{job_id}", response_model=JobRead)
def get_job(job_id: str, db: Session = Depends(get_db)) -> JobRead:
    return _to_read_model(get_job_or_404(db, job_id))


@router.get("/{job_id}/progress")
def stream_job_progress(job_id: str, db: Session = Depends(get_db)) -> StreamingResponse:
    """Server-sent events carrying this job's progress until it finishes.

    Each event is a full JobRead, not a delta, so a client that drops a
    frame or reconnects is immediately correct again.
    """
    get_job_or_404(db, job_id)

    async def events():
        # A session per iteration rather than one held open for the life
        # of the stream: this connection may last hours, and the worker
        # needs the write lock far more than we need a cached read.
        while True:
            with SessionLocal() as session:
                job = session.get(Job, job_id)
                if job is None:
                    break
                payload = _to_read_model(job)
                terminal = job.is_terminal

            yield f"data: {payload.model_dump_json()}\n\n"
            if terminal:
                break
            await asyncio.sleep(_STREAM_POLL_SECONDS)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # Without this a proxy may sit on the stream until it ends,
            # which would defeat the point of streaming it.
            "X-Accel-Buffering": "no",
        },
    )
