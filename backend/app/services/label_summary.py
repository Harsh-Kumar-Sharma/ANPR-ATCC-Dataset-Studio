"""What a labeller has actually produced, across a whole project.

Not the same question as the evaluation report's class distribution, and
that is the point. That one is scoped to a processing *run*, which is
the right unit for asking how a detection pass went - but a box drawn on
the labelling canvas belongs to a frame, and the frame's source may have
several runs, so there is no honest way to attribute it to one. A canvas
labeller asking "what have I got?" could not get an answer at all.

This asks the project-level version instead: every human label a dataset
could contain, whichever way it was written. The two coexist because
they are different questions, not because one is a fallback.
"""

from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models.annotation import Annotation
from app.db.models.frame import Frame
from app.db.models.source import Source
from app.services.class_definitions import class_names_for


@dataclass
class ClassCount:
    class_id: int
    name: str
    box_count: int


@dataclass
class LabelBalance:
    """The shape of a project's labelling, as a labeller would ask it."""

    #: Classes that actually have boxes, most-used first. A class nobody
    #: has used is absent rather than listed as zero - twenty preset
    #: classes of which three are used reads better as three rows.
    classes: list[ClassCount] = field(default_factory=list)
    #: Accepted human boxes carrying a class. What would export.
    total_boxes: int = 0
    #: Boxes a human drew but has not classified. They do not export, and
    #: the gap between "my dataset is small" and "my dataset is small
    #: because forty boxes still need a class" is worth being able to see.
    unclassified_boxes: int = 0
    #: Frames a human has finished, including ones they labelled as
    #: holding nothing.
    labeled_frames: int = 0
    #: Of those, the ones holding nothing. Part of the balance of a
    #: dataset, and the part most easily got wrong by accident.
    background_frames: int = 0


def label_balance(db: Session, project_id: str) -> LabelBalance:
    """Count this project's human labels by class.

    Rejected frames are left out, for the same reason they are left out
    of an export: they are not going to train anything, so counting them
    would describe a dataset that will never exist.

    ``hard`` labels are left out too. This counts what a dataset would
    contain, and a ``hard`` decision is explicitly "not settled" - it
    belongs in the active-learning queue, not in a balance a labeller
    reads as their yield.
    """
    # Written out rather than derived from the annotation select with
    # ``with_only_columns``: replacing the columns of a select that
    # carries its own joins is the sort of thing that quietly changes
    # the FROM clause, and an aggregate is the worst place to find out.
    def scoped(*columns):
        return (
            select(*columns)
            .select_from(Annotation)
            .join(Frame, Annotation.frame_id == Frame.id)
            .join(Source, Frame.source_id == Source.id)
            .where(
                Source.project_id == project_id,
                Frame.status != "rejected",
                Annotation.source == "human",
                Annotation.status == "accepted",
            )
        )

    counts = db.execute(
        scoped(Annotation.class_id, func.count(Annotation.id))
        .where(Annotation.class_id.is_not(None))
        .group_by(Annotation.class_id)
        .order_by(func.count(Annotation.id).desc(), Annotation.class_id)
    ).all()

    names = class_names_for(db, project_id)
    classes = [
        ClassCount(class_id=class_id, name=names.get(class_id, str(class_id)), box_count=count)
        for class_id, count in counts
    ]

    unclassified = db.scalar(scoped(func.count(Annotation.id)).where(Annotation.class_id.is_(None))) or 0

    labeled_frames = (
        db.scalar(
            select(func.count(Frame.id))
            .join(Source, Frame.source_id == Source.id)
            .where(Source.project_id == project_id, Frame.status == "labeled")
        )
        or 0
    )
    frames_with_boxes = (
        db.scalar(
            select(func.count(func.distinct(Annotation.frame_id)))
            .select_from(Annotation)
            .join(Frame, Annotation.frame_id == Frame.id)
            .join(Source, Frame.source_id == Source.id)
            .where(Source.project_id == project_id, Frame.status == "labeled", Annotation.source == "human")
        )
        or 0
    )

    return LabelBalance(
        classes=classes,
        total_boxes=sum(c.box_count for c in classes),
        unclassified_boxes=unclassified,
        labeled_frames=labeled_frames,
        # A labelled frame with no human box on it is one somebody saved
        # empty. Derived rather than counted separately so the two can
        # never disagree about the same frames.
        background_frames=max(0, labeled_frames - frames_with_boxes),
    )
