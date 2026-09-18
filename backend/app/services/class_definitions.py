"""Reading and seeding a project's own class list.

Every consumer that used to call ``get_class_schema(version)`` against a
hardcoded module now comes through here, so there is exactly one answer
to "what can this project label with", and it is the project's own rows.
"""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import AppError, ConflictError, NotFoundError
from app.core.presets import get_preset
from app.db.models.annotation import Annotation
from app.db.models.class_definition import ClassDefinition
from app.services.dataset_query import project_annotations


class InvalidClassNameError(AppError):
    """A name that cannot be stored at all, as opposed to one that
    clashes. Coded so the editor shows the reason rather than an
    anonymous failure."""

    code = "invalid_class_name"


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
        raise InvalidClassNameError("A class name cannot be blank.")
    return trimmed


def _comparable(name: str) -> str:
    """The form two names are judged equal by.

    ``casefold`` rather than ``lower``: it is the Unicode-aware one, and
    it folds cases ``lower`` leaves alone (German "Straße" and "STRASSE"
    are the same word).
    """
    return name.strip().casefold()


def _find_by_name(db: Session, project_id: str, name: str) -> ClassDefinition | None:
    """Find a class by name, compared case-insensitively.

    Compared in Python, not SQL, and deliberately. SQLite's ``lower()``
    only touches A-Z, so an accented capital survived it and a stored
    "VÉHICULE" never matched an incoming "véhicule" - while the table's
    own uniqueness constraint is case-sensitive and did not catch it
    either. A project's class list is tens of rows, so comparing them in
    Python costs nothing and is simply correct.
    """
    wanted = _comparable(name)
    for existing in list_project_classes(db, project_id):
        if _comparable(existing.name) == wanted:
            return existing
    return None


def get_class(db: Session, project_id: str, class_id: int) -> ClassDefinition:
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

    The new class_id is one past the highest currently in the project.
    That can reuse the id of a deleted class, which is safe precisely
    because deletion is refused while anything still points at one - by
    the time an id is free, nothing means it.
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
    target = get_class(db, project_id, class_id)

    clash = _find_by_name(db, project_id, name)
    if clash is not None and clash.id != target.id:
        raise DuplicateClassNameError(f"This project already has a class called {name!r}.")

    target.name = name
    db.flush()
    return target


def count_labels_using(db: Session, project_id: str, class_id: int) -> int:
    """How many of this project's annotations point at this class.

    Counts *every* annotation holding the id, not just accepted ones. A
    rejected review still carries a class_id, and deleting the class out
    from under it would orphan it just the same. The message says so, so
    a count the user cannot see in their accepted work is at least
    explained.

    Scoped to the project: another project's labels must not make a class
    undeletable here. This is the number ticket 07's "these 47 labels use
    this class" prompt is built from.
    """
    annotations = project_annotations(project_id).where(Annotation.class_id == class_id).subquery()
    return db.scalar(select(func.count()).select_from(annotations)) or 0


def delete_class(db: Session, project_id: str, class_id: int) -> None:
    """Remove a class that nothing is using.

    Refuses while labels still point at it. Deleting those labels, or
    moving them somewhere else, is a decision only the user can make -
    that conversation is ticket 07. Until it exists, refusing beats
    leaving annotations pointing at a class that is gone, which is the
    easiest way to silently poison a dataset.
    """
    target = get_class(db, project_id, class_id)

    in_use = count_labels_using(db, project_id, class_id)
    if in_use:
        raise ClassInUseError(
            f"{in_use} annotation(s) still use {target.name!r}, including any rejected reviews. "
            "Move them to another class or delete them first."
        )

    db.delete(target)
    db.flush()
