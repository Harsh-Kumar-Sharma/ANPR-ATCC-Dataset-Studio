"""Reading and seeding a project's own class list.

Every consumer that used to call ``get_class_schema(version)`` against a
hardcoded module now comes through here, so there is exactly one answer
to "what can this project label with", and it is the project's own rows.
"""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import ConflictError, NotFoundError
from app.core.presets import get_preset
from app.db.models.annotation import Annotation
from app.db.models.class_definition import ClassDefinition
from app.db.models.frame_candidate import FrameCandidate
from app.db.models.processing_run import ProcessingRun
from app.db.models.source import Source
from app.db.models.track import Track


class DuplicateClassNameError(ConflictError):
    """Two classes with the same name are indistinguishable while
    labelling, which is how a dataset ends up split across two
    categories that were meant to be one."""

    code = "duplicate_class_name"


class ClassInUseError(ConflictError):
    """Deleting a class that labels still point at needs the remap
    conversation in ticket 07."""

    code = "class_in_use"


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


def _normalize(name: str) -> str:
    """Trim a name for storage.

    Comparison is case-insensitive on top of this (see ``_find_by_name``):
    "Vehicle" and "vehicle " are the same class to whoever is labelling,
    and letting both exist quietly splits a dataset in half.
    """
    trimmed = name.strip()
    if not trimmed:
        raise ValueError("A class name cannot be blank.")
    return trimmed


def _find_by_name(db: Session, project_id: str, name: str) -> ClassDefinition | None:
    return db.scalar(
        select(ClassDefinition).where(
            ClassDefinition.project_id == project_id,
            func.lower(ClassDefinition.name) == name.lower(),
        )
    )


def _get_class(db: Session, project_id: str, class_id: int) -> ClassDefinition:
    found = db.scalar(
        select(ClassDefinition).where(
            ClassDefinition.project_id == project_id,
            ClassDefinition.class_id == class_id,
        )
    )
    if found is None:
        raise NotFoundError(f"Class {class_id} not found in this project")
    return found


def create_class(db: Session, project_id: str, name: str) -> ClassDefinition:
    """Add a class to the end of the project's list.

    The new class_id is one past the highest ever used, never a gap left
    by a deletion: ids are what labels point at, so reissuing one would
    silently reinterpret every label still holding it.
    """
    name = _normalize(name)
    if _find_by_name(db, project_id, name) is not None:
        raise DuplicateClassNameError(f"This project already has a class called {name!r}.")

    highest_id = db.scalar(
        select(func.max(ClassDefinition.class_id)).where(ClassDefinition.project_id == project_id)
    )
    highest_order = db.scalar(
        select(func.max(ClassDefinition.display_order)).where(ClassDefinition.project_id == project_id)
    )

    created = ClassDefinition(
        project_id=project_id,
        class_id=(highest_id or 0) + 1,
        name=name,
        display_order=(highest_order or 0) + 1,
    )
    db.add(created)
    db.flush()
    return created


def rename_class(db: Session, project_id: str, class_id: int, name: str) -> ClassDefinition:
    """Rename a class. Never touches a label - that is what makes renaming
    the one class edit that is always safe."""
    name = _normalize(name)
    target = _get_class(db, project_id, class_id)

    clash = _find_by_name(db, project_id, name)
    if clash is not None and clash.id != target.id:
        raise DuplicateClassNameError(f"This project already has a class called {name!r}.")

    target.name = name
    db.flush()
    return target


def count_labels_using(db: Session, project_id: str, class_id: int) -> int:
    """How many of this project's annotations point at this class.

    Scoped to the project: another project's labels must not make a class
    undeletable here. This is the number ticket 07's "these 47 labels use
    this class" prompt is built from.
    """
    return (
        db.scalar(
            select(func.count(Annotation.id))
            .join(FrameCandidate, Annotation.frame_candidate_id == FrameCandidate.id)
            .join(Track, FrameCandidate.track_id == Track.id)
            .join(ProcessingRun, Track.run_id == ProcessingRun.id)
            .join(Source, ProcessingRun.source_id == Source.id)
            .where(Source.project_id == project_id, Annotation.class_id == class_id)
        )
        or 0
    )


def delete_class(db: Session, project_id: str, class_id: int) -> None:
    """Remove a class that nothing is using.

    Refuses while labels still point at it. Deleting those labels, or
    moving them somewhere else, is a decision only the user can make -
    that conversation is ticket 07. Until it exists, refusing beats
    leaving annotations pointing at a class that is gone, which is the
    easiest way to silently poison a dataset.
    """
    target = _get_class(db, project_id, class_id)

    in_use = count_labels_using(db, project_id, class_id)
    if in_use:
        raise ClassInUseError(
            f"{in_use} label(s) still use {target.name!r}. Move them to another class or delete them first."
        )

    db.delete(target)
    db.flush()
