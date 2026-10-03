"""Labelling tasks: a source's frames, given to one person.

Who may see what follows from here too. An admin sees everything; a
user sees the projects and sources they have a task on, and nothing
else. See ``app.api.access``.
"""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError, ConflictError, NotFoundError
from app.db.models.labeling_task import (
    TASK_ASSIGNED,
    TASK_DONE,
    TASK_IN_PROGRESS,
    TASK_IN_REVIEW,
    LabelingTask,
)
from app.db.models.source import Source
from app.db.models.user import ROLE_ADMIN, User
from app.services.frames import queue_progress


class TaskStateError(AppError):
    status_code = 409
    code = "task_state"


class ForbiddenTaskError(AppError):
    status_code = 403
    code = "forbidden"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def is_admin(user: User) -> bool:
    return user.role == ROLE_ADMIN


def get_task_or_404(db: Session, task_id: str) -> LabelingTask:
    task = db.get(LabelingTask, task_id)
    if task is None:
        raise NotFoundError(f"Task not found: {task_id}")
    return task


# --- what a user may reach --------------------------------------------------


def source_ids_for(db: Session, user: User) -> set[str]:
    return set(db.scalars(select(LabelingTask.source_id).where(LabelingTask.assignee_id == user.id)))


def project_ids_for(db: Session, user: User) -> set[str]:
    return set(db.scalars(select(LabelingTask.project_id).where(LabelingTask.assignee_id == user.id)))


def task_for_source(db: Session, source_id: str) -> LabelingTask | None:
    return db.scalar(select(LabelingTask).where(LabelingTask.source_id == source_id))


# --- creating and handing out -------------------------------------------------


def create_task(db: Session, project_id: str, source_id: str, assignee: User, created_by: User) -> LabelingTask:
    source = db.get(Source, source_id)
    if source is None or source.project_id != project_id:
        raise NotFoundError(f"Source not found in this project: {source_id}")
    if task_for_source(db, source_id) is not None:
        raise ConflictError("This source already has a task. Reassign that one instead.", code="task_exists")
    _require_active(assignee)
    task = LabelingTask(
        project_id=project_id,
        source_id=source_id,
        assignee_id=assignee.id,
        status=TASK_ASSIGNED,
        created_by_id=created_by.id,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


def reassign(db: Session, task: LabelingTask, assignee: User) -> LabelingTask:
    _require_active(assignee)
    task.assignee_id = assignee.id
    db.commit()
    db.refresh(task)
    return task


def _require_active(user: User) -> None:
    if not user.is_active:
        raise ConflictError(f"{user.username} is switched off. Switch them on first, or pick someone else.")


# --- moving through the states ----------------------------------------------


def _require_status(task: LabelingTask, *allowed: str) -> None:
    if task.status not in allowed:
        raise TaskStateError(f"This task is {task.status.replace('_', ' ')}, so that cannot be done now.")


def _require_assignee(task: LabelingTask, user: User) -> None:
    if task.assignee_id != user.id:
        raise ForbiddenTaskError("This task is not yours.")


def mark_started(db: Session, task: LabelingTask) -> None:
    """The assignee has begun. Idempotent: only an untouched task moves."""
    if task.status != TASK_ASSIGNED:
        return
    task.status = TASK_IN_PROGRESS
    task.started_at = _utcnow()
    db.commit()


def start(db: Session, task: LabelingTask, user: User) -> LabelingTask:
    _require_assignee(task, user)
    mark_started(db, task)
    db.refresh(task)
    return task


def submit(db: Session, task: LabelingTask, user: User) -> LabelingTask:
    _require_assignee(task, user)
    _require_status(task, TASK_ASSIGNED, TASK_IN_PROGRESS)
    pending = progress(db, task)["pending"]
    if pending > 0:
        raise TaskStateError(
            f"{pending} frame(s) are still waiting. Label or skip them before submitting.", code="frames_pending"
        )
    task.status = TASK_IN_REVIEW
    task.submitted_at = _utcnow()
    task.started_at = task.started_at or task.submitted_at
    task.review_note = None
    db.commit()
    db.refresh(task)
    return task


def accept(db: Session, task: LabelingTask) -> LabelingTask:
    _require_status(task, TASK_IN_REVIEW)
    task.status = TASK_DONE
    task.completed_at = _utcnow()
    db.commit()
    db.refresh(task)
    return task


def reject(db: Session, task: LabelingTask, note: str) -> LabelingTask:
    _require_status(task, TASK_IN_REVIEW)
    if not note.strip():
        raise TaskStateError("Say what needs fixing, so they know what to look at.", code="note_required")
    task.status = TASK_IN_PROGRESS
    task.review_note = note.strip()
    db.commit()
    db.refresh(task)
    return task


def reopen(db: Session, task: LabelingTask) -> LabelingTask:
    _require_status(task, TASK_DONE)
    task.status = TASK_IN_PROGRESS
    task.completed_at = None
    db.commit()
    db.refresh(task)
    return task


def delete_task(db: Session, task: LabelingTask) -> None:
    """The task only. Its frames and their labels are the source's."""
    db.delete(task)
    db.commit()


# --- reading ---------------------------------------------------------------


def progress(db: Session, task: LabelingTask) -> dict[str, int]:
    return queue_progress(db, task.project_id, source_id=task.source_id)


def list_for_project(db: Session, project_id: str) -> list[LabelingTask]:
    return list(
        db.scalars(select(LabelingTask).where(LabelingTask.project_id == project_id).order_by(LabelingTask.created_at))
    )


def list_for_user(db: Session, user: User, project_id: str | None = None) -> list[LabelingTask]:
    query = select(LabelingTask).where(LabelingTask.assignee_id == user.id)
    if project_id is not None:
        query = query.where(LabelingTask.project_id == project_id)
    return list(db.scalars(query.order_by(LabelingTask.created_at)))
