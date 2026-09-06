from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.class_schema import get_class_schema
from app.core.config import get_settings
from app.core.errors import NotFoundError
from app.db.models.project import Project
from app.db.session import get_db
from app.schemas.project import ProjectCreate, ProjectRead
from app.services.workspace import create_project_workspace

router = APIRouter(prefix="/projects", tags=["projects"])


def get_project_or_404(db: Session, project_id: str) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise NotFoundError(f"Project not found: {project_id}")
    return project


@router.post("", response_model=ProjectRead, status_code=201)
def create_project(payload: ProjectCreate, db: Session = Depends(get_db)) -> Project:
    project = Project(name=payload.name, workspace_path="")
    db.add(project)
    db.flush()  # assign project.id without committing yet

    settings = get_settings()
    workspace_path = create_project_workspace(settings.workspace_root, project.id, project.name)
    project.workspace_path = str(workspace_path)

    db.commit()
    db.refresh(project)
    return project


@router.get("", response_model=list[ProjectRead])
def list_projects(db: Session = Depends(get_db)) -> list[Project]:
    return list(db.scalars(select(Project).order_by(Project.created_at.desc())))


@router.get("/{project_id}", response_model=ProjectRead)
def get_project(project_id: str, db: Session = Depends(get_db)) -> Project:
    return get_project_or_404(db, project_id)


@router.get("/{project_id}/class-schema", response_model=list[dict])
def get_project_class_schema(project_id: str, db: Session = Depends(get_db)) -> list[dict]:
    project = get_project_or_404(db, project_id)
    return get_class_schema(project.class_schema_version)
