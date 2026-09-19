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

from sqlalchemy import func, or_, select
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

    # A frame somebody has worked on, whichever way they did it.
    # `Frame.status` is set to "labeled" only by a canvas save - track
    # review never touches it - so counting status alone reported
    # "nineteen boxes across zero labelled frames" for a project that
    # had been reviewed track by track, which is what the real data
    # looks like. Carrying a human box counts too, and the distinct
    # count keeps a frame done both ways from being counted twice.
    worked_on = (
        select(func.count(func.distinct(Frame.id)))
        .select_from(Frame)
        .join(Source, Frame.source_id == Source.id)
        .outerjoin(Annotation, (Annotation.frame_id == Frame.id) & (Annotation.source == "human"))
        .where(
            Source.project_id == project_id,
            Frame.status != "rejected",
            or_(Frame.status == "labeled", Annotation.id.is_not(None)),
        )
    )
    labeled_frames = db.scalar(worked_on) or 0

    # Empty on purpose: a human saved the frame and it holds no boxes at
    # all. Deliberately "no annotation rows whatsoever" rather than "no
    # human ones", the same rule `query_export_frames` uses and for the
    # same reason - a frame carrying a box of any kind is not an empty
    # picture, and the weaker rule is how a hard-reviewed vehicle became
    # a background image.
    background_frames = (
        db.scalar(
            select(func.count(Frame.id))
            .select_from(Frame)
            .join(Source, Frame.source_id == Source.id)
            .where(
                Source.project_id == project_id,
                Frame.status == "labeled",
                Frame.id.not_in(select(Annotation.frame_id)),
            )
        )
        or 0
    )

    return LabelBalance(
        classes=classes,
        total_boxes=sum(c.box_count for c in classes),
        unclassified_boxes=unclassified,
        labeled_frames=labeled_frames,
        background_frames=background_frames,
    )
