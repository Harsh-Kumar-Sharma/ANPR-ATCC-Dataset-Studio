"""Labelling tasks: giving a source's frames to someone, and seeing
them through to done.

Admins create, hand out, review and reopen. The assignee starts and
submits. A user only ever sees their own tasks.
"""

from datetime import datetime

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.access import require_admin
from app.api.auth import require_user
from app.api.projects import get_project_or_404
from app.core.errors import NotFoundError
from app.db.models.labeling_task import LabelingTask
from app.db.models.project import Project
from app.db.models.source import Source
from app.db.models.user import User
from app.db.session import get_db
from app.services import auth as auth_service
from app.services import labeling_tasks
from app.services.auth import ForbiddenError

project_tasks_router = APIRouter(prefix="/projects/{project_id}/tasks", tags=["tasks"])
tasks_router = APIRouter(prefix="/tasks", tags=["tasks"])


class TaskPerson(BaseModel):
    id: str
    username: str
    display_name: str


class TaskProgress(BaseModel):
    pending: int
    labeled: int
    rejected: int
    skipped: int
    total: int


class TaskRead(BaseModel):
    id: str
    project_id: str
    project_name: str
    source_id: str
    source_type: str
    source_path_or_uri: str
    source_name: str | None
    assignee: TaskPerson | None
    status: str
    review_note: str | None
    progress: TaskProgress
    created_at: datetime
    started_at: datetime | None
    submitted_at: datetime | None
    completed_at: datetime | None


class TaskCreate(BaseModel):
    source_id: str
    assignee_id: str


class TaskReassign(BaseModel):
    assignee_id: str


class TaskReject(BaseModel):
    note: str


def _read(db: Session, task: LabelingTask) -> TaskRead:
    project = db.get(Project, task.project_id)
    source = db.get(Source, task.source_id)
    assignee = db.get(User, task.assignee_id) if task.assignee_id else None
    return TaskRead(
        id=task.id,
        project_id=task.project_id,
        project_name=project.name if project else "",
        source_id=task.source_id,
        source_type=source.type if source else "",
        source_path_or_uri=source.path_or_uri if source else "",
        source_name=source.name if source else None,
        assignee=TaskPerson(id=assignee.id, username=assignee.username, display_name=assignee.display_name)
        if assignee
        else None,
        status=task.status,
        review_note=task.review_note,
        progress=TaskProgress(**labeling_tasks.progress(db, task)),
        created_at=task.created_at,
        started_at=task.started_at,
        submitted_at=task.submitted_at,
        completed_at=task.completed_at,
    )


def _visible_task(db: Session, task_id: str, user: User) -> LabelingTask:
    task = labeling_tasks.get_task_or_404(db, task_id)
    if not labeling_tasks.is_admin(user) and task.assignee_id != user.id:
        # Not "forbidden": a user should not learn which ids exist.
        raise NotFoundError(f"Task not found: {task_id}")
    return task


# --- a project's tasks -------------------------------------------------------


@project_tasks_router.get("", response_model=list[TaskRead])
def list_tasks(project_id: str, db: Session = Depends(get_db), user: User = Depends(require_user)) -> list[TaskRead]:
    """Every task in the project for an admin; your own for a user."""
    get_project_or_404(db, project_id)
    if labeling_tasks.is_admin(user):
        tasks = labeling_tasks.list_for_project(db, project_id)
    else:
        tasks = labeling_tasks.list_for_user(db, user, project_id)
    return [_read(db, task) for task in tasks]


@project_tasks_router.post("", response_model=TaskRead, status_code=201)
def create_task(
    project_id: str, payload: TaskCreate, db: Session = Depends(get_db), admin: User = Depends(require_admin)
) -> TaskRead:
    get_project_or_404(db, project_id)
    assignee = auth_service.get_user_or_404(db, payload.assignee_id)
    return _read(db, labeling_tasks.create_task(db, project_id, payload.source_id, assignee, admin))


# --- one task -----------------------------------------------------------------


@tasks_router.get("/mine", response_model=list[TaskRead])
def my_tasks(db: Session = Depends(get_db), user: User = Depends(require_user)) -> list[TaskRead]:
    return [_read(db, task) for task in labeling_tasks.list_for_user(db, user)]


@tasks_router.get("/{task_id}", response_model=TaskRead)
def get_task(task_id: str, db: Session = Depends(get_db), user: User = Depends(require_user)) -> TaskRead:
    return _read(db, _visible_task(db, task_id, user))


@tasks_router.patch("/{task_id}", response_model=TaskRead)
def reassign_task(
    task_id: str, payload: TaskReassign, db: Session = Depends(get_db), _: User = Depends(require_admin)
) -> TaskRead:
    task = labeling_tasks.get_task_or_404(db, task_id)
    assignee = auth_service.get_user_or_404(db, payload.assignee_id)
    return _read(db, labeling_tasks.reassign(db, task, assignee))


@tasks_router.post("/{task_id}/start", response_model=TaskRead)
def start_task(task_id: str, db: Session = Depends(get_db), user: User = Depends(require_user)) -> TaskRead:
    task = _visible_task(db, task_id, user)
    if labeling_tasks.is_admin(user) and task.assignee_id != user.id:
        # An admin opening someone else's task to look is not them starting it.
        return _read(db, task)
    return _read(db, labeling_tasks.start(db, task, user))


@tasks_router.post("/{task_id}/submit", response_model=TaskRead)
def submit_task(task_id: str, db: Session = Depends(get_db), user: User = Depends(require_user)) -> TaskRead:
    task = _visible_task(db, task_id, user)
    if task.assignee_id != user.id:
        raise ForbiddenError("Only the person the task is assigned to can submit it.")
    return _read(db, labeling_tasks.submit(db, task, user))


@tasks_router.post("/{task_id}/accept", response_model=TaskRead)
def accept_task(task_id: str, db: Session = Depends(get_db), _: User = Depends(require_admin)) -> TaskRead:
    return _read(db, labeling_tasks.accept(db, labeling_tasks.get_task_or_404(db, task_id)))


@tasks_router.post("/{task_id}/reject", response_model=TaskRead)
def reject_task(
    task_id: str, payload: TaskReject, db: Session = Depends(get_db), _: User = Depends(require_admin)
) -> TaskRead:
    return _read(db, labeling_tasks.reject(db, labeling_tasks.get_task_or_404(db, task_id), payload.note))


@tasks_router.post("/{task_id}/reopen", response_model=TaskRead)
def reopen_task(task_id: str, db: Session = Depends(get_db), _: User = Depends(require_admin)) -> TaskRead:
    return _read(db, labeling_tasks.reopen(db, labeling_tasks.get_task_or_404(db, task_id)))


@tasks_router.delete("/{task_id}", status_code=204)
def delete_task(task_id: str, db: Session = Depends(get_db), _: User = Depends(require_admin)) -> Response:
    labeling_tasks.delete_task(db, labeling_tasks.get_task_or_404(db, task_id))
    return Response(status_code=204)
