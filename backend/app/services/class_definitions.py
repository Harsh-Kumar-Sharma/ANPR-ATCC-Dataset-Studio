"""Reading and seeding a project's own class list.

Every consumer that used to call ``get_class_schema(version)`` against a
hardcoded module now comes through here, so there is exactly one answer
to "what can this project label with", and it is the project's own rows.
"""

from dataclasses import dataclass

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.core.errors import AppError, ConflictError, NotFoundError
from app.core.presets import get_preset
from app.db.models.annotation import Annotation
from app.db.models.class_definition import ClassDefinition
from app.db.models.dataset_item import DatasetItem
from app.db.models.frame_candidate import FrameCandidate
from app.db.models.track import Track
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


class InvalidRemapError(AppError):
    """A delete request that cannot be carried out as asked - both
    options at once, or a class merged into itself."""

    code = "invalid_remap"


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


@dataclass(frozen=True)
class DeleteOutcome:
    """What deleting a class did to the labels that were using it."""

    remapped: int
    deleted_labels: int


def delete_class(
    db: Session,
    project_id: str,
    class_id: int,
    *,
    remap_to: int | None = None,
    delete_labels: bool = False,
) -> DeleteOutcome:
    """Remove a class, and settle every label that was using it.

    A class nothing points at is simply removed. One that labels still
    use needs the user to say what happens to them, and there are
    exactly two answers:

    ``remap_to`` moves every label onto another class. "Merge B into A"
    is this same call - there is deliberately no second path for it.

    ``delete_labels`` removes the labels along with the class. Their
    tracks stop claiming a review that no longer exists, and any dataset
    item indexing them goes too: SQLite is not enforcing foreign keys
    here, so it would otherwise sit there silently pointing at nothing.
    The export on disk is the durable record of what a version
    contained; the item row is only an index into it.

    With neither, a class in use is refused. Leaving labels pointing at a
    class that is gone is the easiest way to silently poison a dataset,
    and this function never does it - which is the invariant everything
    else here is in service of.
    """
    if remap_to is not None and delete_labels:
        raise InvalidRemapError("Choose one: move the labels to another class, or delete them.")

    target = get_class(db, project_id, class_id)

    in_use = count_labels_using(db, project_id, class_id)
    if in_use and remap_to is None and not delete_labels:
        raise ClassInUseError(
            f"{in_use} annotation(s) still use {target.name!r}, including any rejected reviews. "
            "Move them to another class or delete them first."
        )

    remapped = 0
    deleted = 0
    if in_use and remap_to is not None:
        remapped = _remap_labels(db, project_id, class_id, remap_to)
    elif in_use and delete_labels:
        deleted = _delete_labels(db, project_id, class_id)

    db.delete(target)
    db.flush()
    return DeleteOutcome(remapped=remapped, deleted_labels=deleted)


def _remap_labels(db: Session, project_id: str, from_class_id: int, to_class_id: int) -> int:
    if from_class_id == to_class_id:
        raise InvalidRemapError("A class cannot be merged into itself.")
    # Validated before anything moves: remapping onto a class that does
    # not exist would orphan every label it touched.
    get_class(db, project_id, to_class_id)

    affected = _annotation_ids_using(db, project_id, from_class_id)
    if not affected:
        return 0
    db.execute(update(Annotation).where(Annotation.id.in_(affected)).values(class_id=to_class_id))
    db.flush()
    return len(affected)


def _delete_labels(db: Session, project_id: str, class_id: int) -> int:
    affected = _annotation_ids_using(db, project_id, class_id)
    if not affected:
        return 0

    # The tracks these labels reviewed. Reset before the labels go, while
    # the join from annotation to track still resolves.
    track_ids = list(
        db.scalars(
            select(FrameCandidate.track_id)
            .join(Annotation, Annotation.frame_candidate_id == FrameCandidate.id)
            .where(Annotation.id.in_(affected))
            .distinct()
        )
    )

    db.execute(delete(DatasetItem).where(DatasetItem.annotation_id.in_(affected)))
    db.execute(delete(Annotation).where(Annotation.id.in_(affected)))
    if track_ids:
        db.execute(update(Track).where(Track.id.in_(track_ids)).values(review_status="unreviewed"))
    db.flush()
    return len(affected)


def _annotation_ids_using(db: Session, project_id: str, class_id: int) -> list[str]:
    """Ids of every annotation in the project that points at this class.

    Materialised as a list rather than left as a subquery so the same
    set drives the count, the update and the delete - a label written
    between those steps is not silently caught by one and missed by
    another.
    """
    return list(
        db.scalars(
            project_annotations(project_id).where(Annotation.class_id == class_id).with_only_columns(Annotation.id)
        )
    )
