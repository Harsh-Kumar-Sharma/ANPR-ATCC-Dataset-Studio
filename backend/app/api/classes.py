"""Editing a project's class list.

Reading a project's classes also happens through
``/projects/{id}/class-schema``, which serves the flatter shape the
review UI already speaks. This router is the editor's view: full rows,
and the writes.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.projects import get_project_or_404
from app.db.models.class_definition import ClassDefinition
from app.db.session import get_db
from app.schemas.class_definition import (
    ClassDefinitionCreate,
    ClassDefinitionRead,
    ClassDefinitionRename,
    ClassUsage,
)
from app.services import class_definitions

router = APIRouter(prefix="/projects/{project_id}/classes", tags=["classes"])


@router.get("", response_model=list[ClassDefinitionRead])
def list_classes(project_id: str, db: Session = Depends(get_db)) -> list[ClassDefinition]:
    get_project_or_404(db, project_id)
    return class_definitions.list_project_classes(db, project_id)


@router.post("", response_model=ClassDefinitionRead, status_code=201)
def create_class(project_id: str, payload: ClassDefinitionCreate, db: Session = Depends(get_db)) -> ClassDefinition:
    get_project_or_404(db, project_id)
    created = class_definitions.create_class(db, project_id, payload.name)
    db.commit()
    db.refresh(created)
    return created


@router.patch("/{class_id}", response_model=ClassDefinitionRead)
def rename_class(
    project_id: str, class_id: int, payload: ClassDefinitionRename, db: Session = Depends(get_db)
) -> ClassDefinition:
    """Rename a class. Labels are untouched - they point at the id."""
    get_project_or_404(db, project_id)
    renamed = class_definitions.rename_class(db, project_id, class_id, payload.name)
    db.commit()
    db.refresh(renamed)
    return renamed


@router.get("/{class_id}/usage", response_model=ClassUsage)
def get_class_usage(project_id: str, class_id: int, db: Session = Depends(get_db)) -> ClassUsage:
    """How many labels this class is holding.

    The editor asks before offering a delete, so it can tell the user
    what is at stake instead of letting them attempt it and fail.
    """
    get_project_or_404(db, project_id)
    # 404s for an unknown class, like rename and delete - otherwise this
    # cheerfully reports zero labels for a class that never existed.
    class_definitions.get_class(db, project_id, class_id)
    return ClassUsage(class_id=class_id, label_count=class_definitions.count_labels_using(db, project_id, class_id))


@router.delete("/{class_id}", status_code=204)
def delete_class(project_id: str, class_id: int, db: Session = Depends(get_db)) -> None:
    """Delete a class nothing is using.

    Refuses with ``class_in_use`` while labels still point at it. Moving
    or deleting those labels is a decision only the user can make, and
    that conversation is ticket 07.
    """
    get_project_or_404(db, project_id)
    class_definitions.delete_class(db, project_id, class_id)
    db.commit()
