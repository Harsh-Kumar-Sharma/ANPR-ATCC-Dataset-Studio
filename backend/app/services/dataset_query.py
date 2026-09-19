from dataclasses import dataclass, field

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.db.models.annotation import Annotation
from app.db.models.frame import Frame
from app.db.models.source import Source


@dataclass
class ExportFrame:
    """One image's worth of dataset: a frame, its source, and every
    accepted box on it.

    The frame is the unit, not the track. Two vehicles in one frame are
    one image with two boxes; a track that runs across forty frames
    contributes a box to whichever of those frames a human labelled. A
    track-keyed result could express neither.

    ``annotations`` is empty for a frame a human labelled as holding
    nothing. That is a real label - the negative example a detector
    needs - and it is not the same as a frame nobody has opened.
    """

    frame: Frame
    source: Source
    annotations: list[Annotation] = field(default_factory=list)


def project_annotations(project_id: str):
    """A select over every annotation belonging to a project.

    Through the frame, not the candidate: a box drawn on the canvas has
    no candidate, and a join through one would make those labels
    invisible to anything asking "whose annotation is this" - including
    the check that stops a class being deleted while labels still use
    it. Written once here so that answer cannot drift between callers.
    """
    return (
        select(Annotation)
        .join(Frame, Annotation.frame_id == Frame.id)
        .join(Source, Frame.source_id == Source.id)
        .where(Source.project_id == project_id)
    )


def project_annotations_for_export(project_id: str):
    """The project's annotations that a dataset could actually contain.

    A label on a rejected frame is excluded, because rejecting a frame
    means it is not worth labelling and that has to reach anything
    reasoning about the dataset - not just the export itself, or the
    two disagree about what exists.
    """
    return project_annotations(project_id).where(Frame.status != "rejected")


def _accepted_annotations(project_id: str):
    """Human truth, on frames a dataset may contain.

    ``model`` annotations are predictions a human has not confirmed, and
    a ``pending`` or ``hard`` one is a decision not yet made; neither is
    label data. Tracks reviewed ``hard`` or ``failed`` fall out here for
    the same reason - they never produced an accepted annotation - which
    keeps them in the database for Phase 8 active learning without
    shipping them in a dataset.
    """
    return project_annotations_for_export(project_id).where(
        Annotation.source == "human",
        Annotation.status == "accepted",
    )


def query_export_frames(db: Session, project_id: str) -> list[ExportFrame]:
    """Every frame of a project that belongs in a dataset, each with its
    complete set of accepted boxes.

    Frame-level, deliberately. The export used to ask this question
    through the annotation's frame *candidate* - annotation to candidate
    to track to run to source - which is track-keyed and could only ever
    see labels written by track review. A box drawn on the labelling
    canvas has no candidate, so an afternoon's labelling exported as
    nothing at all. Joining through ``Annotation.frame_id`` instead sees
    both, because both are boxes on a frame.

    A frame qualifies two ways:

    * it carries at least one accepted human box, or
    * a human labelled it and saved nothing, which says "no vehicles
      here" and is a background image rather than an absence of work.

    Rejected frames are excluded whichever way they would have
    qualified: rejecting means "not worth labelling", and if that did
    not reach the dataset it would be a judgement with no effect. The
    labels themselves survive, so putting the frame back restores them.

    Frames nobody has opened are excluded by both clauses - they are
    ``pending`` with no boxes.
    """
    frame_stmt = (
        select(Frame, Source)
        .join(Source, Frame.source_id == Source.id)
        .where(
            Source.project_id == project_id,
            Frame.status != "rejected",
            or_(
                Frame.status == "labeled",
                # Not scoped to the project or to frame status: the outer
                # query already is, and repeating the frame join inside a
                # subquery is how you end up correlating it by accident.
                Frame.id.in_(
                    select(Annotation.frame_id).where(
                        Annotation.source == "human",
                        Annotation.status == "accepted",
                    )
                ),
            ),
        )
        .order_by(Frame.source_id, Frame.frame_index)
    )
    by_frame = {
        frame.id: ExportFrame(frame=frame, source=source) for frame, source in db.execute(frame_stmt).all()
    }
    if not by_frame:
        return []

    # Ordered by id so a frame's boxes land in the label file in a stable
    # order - a dataset that reshuffles its own lines between exports is
    # not reproducible, whatever the split seed says.
    for annotation in db.scalars(_accepted_annotations(project_id).order_by(Annotation.id)):
        entry = by_frame.get(annotation.frame_id)
        if entry is not None:
            entry.annotations.append(annotation)

    return list(by_frame.values())
