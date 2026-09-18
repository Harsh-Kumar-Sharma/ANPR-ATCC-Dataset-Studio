"""Reading and seeding a project's own class list.

Every consumer that used to call ``get_class_schema(version)`` against a
hardcoded module now comes through here, so there is exactly one answer
to "what can this project label with", and it is the project's own rows.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.presets import get_preset
from app.db.models.class_definition import ClassDefinition


def list_project_classes(db: Session, project_id: str) -> list[ClassDefinition]:
    """The project's classes, in the order the user arranged them."""
    return list(
        db.scalars(
            select(ClassDefinition)
            .where(ClassDefinition.project_id == project_id)
            .order_by(ClassDefinition.display_order, ClassDefinition.class_id)
        )
    )


def class_schema_for(db: Session, project_id: str) -> list[dict]:
    """The project's classes as ``{"id", "name"}`` dicts.

    The shape export, evaluation and the review UI already speak. Kept so
    moving classes into the database did not force a simultaneous rewrite
    of every consumer.
    """
    return [{"id": c.class_id, "name": c.name} for c in list_project_classes(db, project_id)]


def class_names_for(db: Session, project_id: str) -> dict[int, str]:
    """This project's class names, keyed by class id."""
    return {c.class_id: c.name for c in list_project_classes(db, project_id)}


def is_valid_class_id(db: Session, project_id: str, class_id: int) -> bool:
    """Is this class one of *this* project's own?

    Scoped per project on purpose: class 20 being meaningful in a traffic
    survey says nothing about whether it means anything in an ANPR
    project that only has two classes.
    """
    return (
        db.scalar(
            select(ClassDefinition.id).where(
                ClassDefinition.project_id == project_id,
                ClassDefinition.class_id == class_id,
            )
        )
        is not None
    )


def seed_project_classes(db: Session, project_id: str, preset_name: str) -> list[ClassDefinition]:
    """Copy a preset into a project.

    A copy, never a reference: a project's classes are its own from this
    moment on, and editing them must not reach into any other project.

    Idempotent in the sense that matters: a project that already has any
    classes is left exactly as it is, so a retried creation cannot double
    the list or overwrite an edit the user has since made. It is a
    presence check, not a completeness check - a half-seeded project
    would stay half-seeded, which nothing can currently produce because
    the rows go in as one flush.

    Does not commit. The caller owns the transaction, so a project and
    its classes can be made to arrive together or not at all.
    """
    classes = get_preset(preset_name)

    existing = list_project_classes(db, project_id)
    if existing:
        return existing

    created = [
        ClassDefinition(
            project_id=project_id,
            class_id=preset_class.class_id,
            name=preset_class.name,
            display_order=order,
        )
        for order, preset_class in enumerate(classes)
    ]
    db.add_all(created)
    db.flush()
    return list_project_classes(db, project_id)
