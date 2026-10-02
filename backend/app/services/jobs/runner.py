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
import signal
import subprocess
import sys
import threading
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import ConflictError, NotFoundError
from app.db.models.job import JOB_TYPES, TERMINAL_JOB_STATUSES, Job
from app.services.jobs import process
from app.services.jobs.handlers import HANDLERS
from app.services.jobs.progress import read_progress

logger = logging.getLogger(__name__)

#: Given a job id, start a worker for it and return its pid. Injected so
#: the lifecycle can be tested without spawning real processes.
Launcher = Callable[[str], int]

#: Is this pid still the live worker for a job created at this time?
#: Injected so reconciliation is testable without real processes.
LivenessProbe = Callable[[int | None, datetime | None], bool]

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
        worker = subprocess.Popen(
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
    watch_worker(worker, lambda returncode: _settle_abandoned_in_own_session(job_id, returncode))
    return worker.pid


def watch_worker(worker: subprocess.Popen, on_exit: Callable[[int], None]) -> threading.Thread:
    """Wait for a worker in the background and report how it exited.

    Waiting is what reaps it. Nothing used to, so a worker that died
    while the app ran stayed behind as a zombie that still answered to
    its pid - and its job said "running" until the app was restarted.
    A worker that outlives the app is unaffected: the thread dies with
    the app, and reconcile_jobs takes over on the next start.
    """

    def wait() -> None:
        returncode = worker.wait()
        try:
            on_exit(returncode)
        except Exception:
            logger.exception("Could not settle after worker %s exited with %s", worker.pid, returncode)

    watcher = threading.Thread(target=wait, name=f"job-worker-{worker.pid}", daemon=True)
    watcher.start()
    return watcher


def _settle_abandoned_in_own_session(job_id: str, returncode: int) -> None:
    # Imported here: the worker process imports this module too, and has
    # no use for a session factory at import time.
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        settle_abandoned(db, job_id, returncode)


def settle_abandoned(db: Session, job_id: str, returncode: int) -> None:
    """Fail a job whose worker exited without recording an outcome.

    The normal end is the worker settling its own job and then exiting,
    and that is left alone. Anything else - killed, crashed before it
    could write - would otherwise claim to be in progress forever.
    """
    job = db.get(Job, job_id)
    if job is None or job.is_terminal:
        return
    logger.warning("Worker for job %s exited with %s without settling it", job_id, returncode)
    job.status = "failed"
    job.error_message = _truncate(_describe_exit(job_id, returncode))
    job.completed_at = _utcnow()
    db.commit()
    db.refresh(job)
    _abort_side_effects(db, job, "failed")


def _describe_exit(job_id: str, returncode: int) -> str:
    # Negative return codes are POSIX signals; Windows has no SIGKILL.
    if returncode == -getattr(signal, "SIGKILL", 9):
        return (
            "The process running this job was killed by the operating system. On a server that almost "
            "always means it ran out of memory - try again with less running alongside it, or a smaller "
            f"model. Its log: {log_path(job_id)}"
        )
    if returncode < 0:
        return f"The process running this job was stopped by signal {-returncode}. Its log: {log_path(job_id)}"
    if returncode == 0:
        return f"The process running this job exited without recording a result. Its log: {log_path(job_id)}"
    return f"The process running this job exited with code {returncode} before finishing. Its log: {log_path(job_id)}"


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
    if _already_settled(job, "succeeded"):
        return job
    job.status = "succeeded"
    job.result_json = result or {}
    job.progress = 1.0
    job.completed_at = _utcnow()
    db.commit()
    db.refresh(job)
    return job


def mark_failed(db: Session, job_id: str, error: str) -> Job:
    job = _get(db, job_id)
    if _already_settled(job, "failed"):
        return job
    job.status = "failed"
    job.error_message = _truncate(error)
    job.completed_at = _utcnow()
    db.commit()
    db.refresh(job)
    return job


class JobRunningError(ConflictError):
    """A job that has not finished cannot be dismissed.

    Its worker is still writing to that row and to those files. Cancel
    it first - that is what cancel is for, and it is the one path that
    stops the process rather than just forgetting about it.
    """

    code = "job_running"


def forget_job(db: Session, job_id: str) -> None:
    """Remove a finished job, its progress file and its worker log.

    Those two files live outside the workspace in the jobs directory,
    so nothing has ever cleaned them up - not a project delete, not a
    source delete. Every job that ever ran has been leaving both behind.
    """
    job = db.get(Job, job_id)
    if job is None:
        return
    if job.status not in TERMINAL_JOB_STATUSES:
        raise JobRunningError(
            f"This job is {job.status}. Cancel it first, then dismiss it."
        )
    _forget_files(job_id)
    db.delete(job)
    db.flush()


def forget_finished_jobs(db: Session, project_id: str) -> int:
    """Remove every finished job of a project. Returns how many went."""
    finished = list(
        db.scalars(
            select(Job).where(Job.project_id == project_id, Job.status.in_(TERMINAL_JOB_STATUSES))
        )
    )
    for job in finished:
        _forget_files(job.id)
        db.delete(job)
    db.flush()
    return len(finished)


def _forget_files(job_id: str) -> None:
    for path in (progress_path(job_id), log_path(job_id)):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            # A log still held open by a worker that has not quite
            # exited. The row is what the user asked to be rid of.
            logger.debug("Could not remove job file %s", path, exc_info=True)


def cancel_job(db: Session, job_id: str, terminate: Callable[[int | None], None] = process.terminate) -> Job:
    """Stop a job and everything it started.

    Cancelling a job that has already finished is a no-op: cancel races
    the work completing on its own, and losing that race must leave the
    outcome alone rather than relabelling a finished run as cancelled.

    The process is killed *before* the row is written, so a worker that
    is mid-write cannot commit a "succeeded" on top of the cancellation.
    """
    job = _get(db, job_id)
    if job.is_terminal:
        logger.info("Job %s is already %s - nothing to cancel", job_id, job.status)
        return job

    terminate(job.pid)

    job.status = "cancelled"
    job.completed_at = _utcnow()
    db.commit()
    db.refresh(job)

    _abort_side_effects(db, job, "cancelled")
    return job


def _abort_side_effects(db: Session, job: Job, outcome: str) -> None:
    """Let the job's own type settle whatever it left behind.

    ``outcome`` is the job's terminal status, and whatever the handler
    owns must end on the same one: a job that failed owning a run that
    claims it was cancelled misreports what happened to the user.

    Dispatched through a registry so adding a job type does not mean
    editing a switch in here.
    """
    handler = HANDLERS.get(job.type)
    if handler is None or handler.on_abort is None:
        return
    try:
        handler.on_abort(db, dict(job.params_json or {}), outcome)
    except Exception:
        # The job has already reached its terminal state as far as the
        # user is concerned; failing to tidy up must not undo that.
        logger.exception("Abort cleanup failed for job %s", job.id)


def reconcile_jobs(db: Session, is_running: LivenessProbe = process.is_running) -> list[str]:
    """Reconcile jobs whose processes may have died while the app was shut.

    Because workers are detached, a job that was running when the app
    closed is usually *still running*, and must be left alone - that is
    the whole point. But a worker that was killed, crashed, or died with
    the machine leaves a row claiming to be in progress forever, and
    that row will also block its source against any future run.

    Returns the ids of the jobs that were marked failed. Ids rather
    than ORM objects: this commits once per job, which expires the
    instances it already handled, and a caller reading them after the
    session closes would get a DetachedInstanceError.
    """
    stale = db.scalars(select(Job).where(Job.status.in_(("pending", "running")))).all()
    reconciled: list[str] = []

    for job in stale:
        # created_at guards against pid reuse: a process older than the
        # job cannot be its worker, however alive it looks.
        if is_running(job.pid, started_after=job.created_at):
            continue
        logger.warning("Job %s claims to be %s but its process is no longer running", job.id, job.status)
        job.status = "failed"
        job.error_message = _truncate(
            "The process running this job is no longer running. It was most likely stopped when the "
            "computer or the app shut down. Start it again."
        )
        job.completed_at = _utcnow()
        # Committed per job together with its own cleanup: a crash
        # halfway through must not leave a failed job owning a run that
        # still claims to be in progress, which is the exact ghost state
        # this function exists to remove.
        db.commit()
        db.refresh(job)
        _abort_side_effects(db, job, "failed")
        reconciled.append(job.id)

    return reconciled


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


def _already_settled(job: Job, attempted: str) -> bool:
    """Has this job already reached a terminal state somebody else wrote?

    The app and the worker write this row from different processes, and
    ``terminate`` returns before the worker has actually died - so a
    cancelled job can still receive a "failed" (from the worker noticing
    it was interrupted) or even a "succeeded" (from a worker that
    finished just as the cancel landed). First terminal state wins;
    later ones are dropped, because a deliberate cancellation must not
    be relabelled as a crash.
    """
    if not job.is_terminal:
        return False
    logger.info("Job %s is already %s - ignoring attempt to mark it %s", job.id, job.status, attempted)
    return True


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
