from dataclasses import asdict

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.auth import require_user
from app.core.config import get_settings
from app.core.errors import NotFoundError
from app.core.presets import DEFAULT_PRESET, get_preset
from app.db.models.project import Project
from app.db.models.user import User
from app.db.session import get_db
from app.schemas.project import ProjectContentsRead, ProjectCreate, ProjectDeleteRequest, ProjectRead
from app.services import labeling_tasks
from app.services.class_definitions import class_schema_for, seed_project_classes
from app.services.project_deletion import delete_project, remove_workspace, summarize
from app.services.workspace import create_project_workspace

router = APIRouter(prefix="/projects", tags=["projects"])

def get_project_or_404(db: Session, project_id: str) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise NotFoundError(f"Project not found: {project_id}")
    return project


@router.post("", response_model=ProjectRead, status_code=201)
def create_project(payload: ProjectCreate, db: Session = Depends(get_db)) -> Project:
    preset = payload.class_preset or DEFAULT_PRESET
    # Validate before creating anything: a project that exists with no
    # usable classes is worse than a rejected request.
    get_preset(preset)

    project = Project(name=payload.name, workspace_path="", class_schema_version=preset)
    db.add(project)
    db.flush()  # assign project.id without committing yet

    settings = get_settings()
    workspace_path = create_project_workspace(settings.workspace_root, project.id, project.name)
    project.workspace_path = str(workspace_path)

    # Copied, not referenced: from here the project owns its classes and
    # editing them can never reach another project. Seeded before the
    # commit so the project and its classes arrive together - a project
    # that exists with no classes is the state this endpoint refuses to
    # create four lines above.
    seed_project_classes(db, project.id, preset)

    db.commit()
    db.refresh(project)
    return project


@router.get("", response_model=list[ProjectRead])
def list_projects(db: Session = Depends(get_db), user: User = Depends(require_user)) -> list[Project]:
    projects = list(db.scalars(select(Project).order_by(Project.created_at.desc())))
    if labeling_tasks.is_admin(user):
        return projects
    # A user sees the projects they have been given work in.
    mine = labeling_tasks.project_ids_for(db, user)
    return [p for p in projects if p.id in mine]


@router.get("/{project_id}", response_model=ProjectRead)
def get_project(project_id: str, db: Session = Depends(get_db)) -> Project:
    return get_project_or_404(db, project_id)


@router.get("/{project_id}/contents", response_model=ProjectContentsRead)
def get_project_contents(project_id: str, db: Session = Depends(get_db)) -> ProjectContentsRead:
    """What deleting this project would destroy.

    Its own endpoint because a confirmation that cannot say what is
    about to go is not a confirmation - and because counting the
    workspace on disk is slow enough that the delete request should not
    be the first time anyone pays for it.
    """
    project = get_project_or_404(db, project_id)
    contents = summarize(db, project, get_settings().workspace_root)
    return ProjectContentsRead(**asdict(contents))


@router.delete("/{project_id}", response_model=ProjectContentsRead)
def remove_project(project_id: str, payload: ProjectDeleteRequest, db: Session = Depends(get_db)) -> ProjectContentsRead:
    """Delete a project and everything it owns, and say what went.

    Irreversible, and not recoverable from an export manifest the way a
    single annotation is. The body must name the project exactly, and
    the request is refused outright while any job is still running
    against it.
    """
    project = get_project_or_404(db, project_id)
    workspace_root = get_settings().workspace_root
    workspace_path = project.workspace_path
    removed = delete_project(db, project, workspace_root, confirm_name=payload.name)
    db.commit()
    # Only now. A filesystem cannot join the transaction, so one of the
    # two failure modes has to be chosen: a crash during a multi-gigabyte
    # delete leaves files the user can remove by hand, where the other
    # order leaves the project in the picker with every row intact and
    # every image gone.
    removed.workspace_removed = remove_workspace(project_id, workspace_path, workspace_root)
    return ProjectContentsRead(**asdict(removed))


@router.get("/{project_id}/class-schema", response_model=list[dict])
def get_project_class_schema(project_id: str, db: Session = Depends(get_db)) -> list[dict]:
    """This project's own classes, in the order it arranged them."""
    get_project_or_404(db, project_id)
    return class_schema_for(db, project_id)
