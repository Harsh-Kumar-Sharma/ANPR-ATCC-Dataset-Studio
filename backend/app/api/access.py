"""Who may reach what, beyond being signed in.

An admin reaches everything. A user labels: they reach the projects
they have a task in, those projects' class lists, and the frames of
their own tasks' sources - nothing else. Enforced here, as router
dependencies, so the existing handlers stay as they were.
"""

import re

from fastapi import Depends, Request
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.api.auth import _token_from, require_user
from app.core.errors import NotFoundError
from app.db.models.frame import Frame
from app.db.models.user import User
from app.db.session import get_db
from app.services import labeling_tasks
from app.services import auth as auth_service
from app.services.auth import AuthError, ForbiddenError

_PROJECT_READS = re.compile(r"^/projects/(?P<project_id>[^/]+)(/class-schema)?/?$")


def require_admin(user: User = Depends(require_user)) -> User:
    if not labeling_tasks.is_admin(user):
        raise ForbiddenError("Only an admin can do this.")
    return user


def schema_upgrade_access(request: Request, db: Session = Depends(get_db)) -> None:
    """Updating the database: open only while nobody can sign in yet.

    The banner has to work before the users table exists - it is what
    creates it - so it cannot always ask for a sign-in. Once anyone
    exists, only an admin may run it: on a server reachable from the
    internet it is otherwise a button any stranger can press.
    """
    if request.method != "POST":
        return
    try:
        if auth_service.user_count(db) == 0:
            return
    except OperationalError:
        db.rollback()
        return  # No users table yet.
    token = _token_from(request)
    if token is None:
        raise AuthError("Sign in as an admin to update the database.", code="not_signed_in")
    user, _ = auth_service.resolve_token(db, token)
    if not labeling_tasks.is_admin(user):
        raise ForbiddenError("Only an admin can update the database.")


def project_access(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)) -> None:
    """The projects router: a user lists projects (filtered in the
    handler) and reads the ones they have a task in."""
    if labeling_tasks.is_admin(user):
        return
    path = request.url.path.rstrip("/")
    if request.method == "GET" and path == "/projects":
        return
    match = _PROJECT_READS.match(path)
    if request.method == "GET" and match:
        if match.group("project_id") in labeling_tasks.project_ids_for(db, user):
            return
        raise NotFoundError(f"Project not found: {match.group('project_id')}")
    raise ForbiddenError("Only an admin can do this.")


def project_frames_access(
    request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)
) -> None:
    """The queue under a project: a user asks about their own sources
    only, one at a time - never the whole project's queue."""
    if labeling_tasks.is_admin(user):
        return
    project_id = request.path_params.get("project_id")
    if project_id not in labeling_tasks.project_ids_for(db, user):
        raise NotFoundError(f"Project not found: {project_id}")
    if request.url.path.rstrip("/").endswith("/by-source"):
        return  # Filtered to their sources in the handler.
    source_id = request.query_params.get("source_id")
    if not source_id:
        raise ForbiddenError("Open one of your tasks to see its frames.")
    if source_id not in labeling_tasks.source_ids_for(db, user):
        raise ForbiddenError("That source is not in any of your tasks.")


def frame_access(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)) -> None:
    """One frame: a user reaches it only through a task they hold.

    Their first touch of a task's frame is what starts the task - an
    admin looking in does not.
    """
    frame_id = request.path_params.get("frame_id")
    if frame_id is None:
        return
    frame = db.get(Frame, frame_id)
    if frame is None:
        return  # The handler says "not found" in its own words.
    task = labeling_tasks.task_for_source(db, frame.source_id)
    if labeling_tasks.is_admin(user):
        return
    if task is None or task.assignee_id != user.id:
        raise ForbiddenError("That frame is not in any of your tasks.")
    labeling_tasks.mark_started(db, task)
