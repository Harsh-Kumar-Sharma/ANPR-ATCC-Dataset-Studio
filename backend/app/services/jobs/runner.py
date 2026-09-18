"""Submitting jobs and moving them through their lifecycle.

The app and the worker are separate processes that share only the
database and a progress file, so every transition here is written to
be safe from either side.

Two decisions worth knowing before changing anything:

**The row is committed before the process is launched.** A worker can
be running and looking for its own row microseconds after launch, and
finding nothing would be an unrecoverable race. Committing first also
means a launch failure has somewhere to record itself.

**The process is detached.** It is deliberately not a child that dies
with the app - closing the window must not kill a training run that has
been going for two hours.
"""

import logging
import os
import subprocess
import sys
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import NotFoundError
from app.db.models.job import JOB_TYPES, Job
from app.services.jobs.progress import read_progress

logger = logging.getLogger(__name__)

#: Given a job id, start a worker for it and return its pid. Injected so
#: the lifecycle can be tested without spawning real processes.
Launcher = Callable[[str], int]

_ERROR_COLUMN_LIMIT = 2048


def progress_path(job_id: str) -> Path:
    """Where the worker for this job reports progress."""
    return Path(get_settings().jobs_dir) / f"{job_id}.progress.json"


def log_path(job_id: str) -> Path:
    """Where the worker's stdout and stderr land.

    A detached process has nowhere to print, and a job that fails with
    nothing but 'exit code 1' is not debuggable.
    """
    return Path(get_settings().jobs_dir) / f"{job_id}.log"


def launch_worker_process(job_id: str) -> int:
    """Start a detached worker process for a job and return its pid."""
    settings = get_settings()
    Path(settings.jobs_dir).mkdir(parents=True, exist_ok=True)

    # Detach so the worker outlives the app. On Windows that means a new
    # process group and no console; on POSIX, its own session.
    creationflags = 0
    start_new_session = False
    if sys.platform == "win32":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    else:
        start_new_session = True

    with open(log_path(job_id), "ab", buffering=0) as log:
        process = subprocess.Popen(
            [sys.executable, "-m", "app.services.jobs.worker", job_id],
            stdout=log,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            cwd=str(Path.cwd()),
            env=os.environ.copy(),
            creationflags=creationflags,
            start_new_session=start_new_session,
            close_fds=True,
        )
    return process.pid


def get_launcher() -> Launcher:
    """How this app starts job workers.

    A FastAPI dependency so it can be overridden - tests swap in a
    launcher that runs the work in-process, because a detached
    subprocess cannot see a stubbed detector injected into this one.
    """
    return launch_worker_process


def submit_job(
    db: Session,
    *,
    type: str,
    project_id: str | None,
    params: dict,
    launcher: Launcher = launch_worker_process,
) -> Job:
    """Persist a job and start a worker for it.

    Raises whatever the launcher raised if the process could not start,
    after marking the job failed - a job stuck in ``pending`` forever is
    indistinguishable from a slow one.
    """
    if type not in JOB_TYPES:
        raise ValueError(f"Unknown job type: {type!r}. Expected one of {', '.join(JOB_TYPES)}.")

    job = Job(type=type, project_id=project_id, params_json=params, status="pending")
    db.add(job)
    db.commit()
    db.refresh(job)

    try:
        job.pid = launcher(job.id)
    except BaseException as exc:
        logger.exception("Could not launch a worker for job %s", job.id)
        job.status = "failed"
        job.error_message = _truncate(f"Could not start the job process: {exc}")
        job.completed_at = _utcnow()
        db.commit()
        raise

    db.commit()
    db.refresh(job)
    return job


def mark_running(db: Session, job_id: str) -> Job:
    job = _get(db, job_id)
    job.status = "running"
    job.started_at = _utcnow()
    db.commit()
    db.refresh(job)
    return job


def mark_succeeded(db: Session, job_id: str, result: dict | None = None) -> Job:
    job = _get(db, job_id)
    job.status = "succeeded"
    job.result_json = result or {}
    job.progress = 1.0
    job.completed_at = _utcnow()
    db.commit()
    db.refresh(job)
    return job


def mark_failed(db: Session, job_id: str, error: str) -> Job:
    job = _get(db, job_id)
    job.status = "failed"
    job.error_message = _truncate(error)
    job.completed_at = _utcnow()
    db.commit()
    db.refresh(job)
    return job


def current_progress(job: Job) -> tuple[float, str | None]:
    """Best available progress for a job.

    While it runs, the worker's file is fresher than the row. Once it is
    finished the row is the only durable answer - progress files are
    transient, and a job that finished last week must still render.
    """
    if job.is_terminal:
        return job.progress, job.progress_message

    reported = read_progress(progress_path(job.id))
    if reported is None:
        return job.progress, job.progress_message
    return reported.fraction, reported.message


def _get(db: Session, job_id: str) -> Job:
    job = db.get(Job, job_id)
    if job is None:
        raise NotFoundError(f"Job not found: {job_id}")
    return job


def _truncate(message: str) -> str:
    if len(message) <= _ERROR_COLUMN_LIMIT:
        return message
    ellipsis = "... [truncated]"
    return message[: _ERROR_COLUMN_LIMIT - len(ellipsis)] + ellipsis


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)
