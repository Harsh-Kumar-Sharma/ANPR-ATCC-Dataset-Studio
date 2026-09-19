from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.annotation import Annotation
from app.db.models.frame import Frame
from app.db.models.frame_candidate import FrameCandidate
from app.db.models.processing_run import ProcessingRun
from app.db.models.source import Source
from app.db.models.track import Track


@dataclass
class ApprovedItem:
    annotation: Annotation
    frame_candidate: FrameCandidate
    track: Track
    source: Source


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


def query_approved_items(db: Session, project_id: str) -> list[ApprovedItem]:
    """Every human-accepted annotation in a project - the dataset-
    eligible set (docs Track Lifecycle step 10: "Approved annotation
    becomes dataset-eligible"). ``hard``/``failed`` tracks are
    preserved in the DB but excluded from export by design - they
    remain available for Phase 8 active learning, just not shipped in
    a training dataset yet.

    A label on a rejected frame is excluded: rejecting a frame means it
    is not worth labelling, and that has to reach the dataset or the
    judgement is decorative. The label itself survives, so putting the
    frame back restores it.

    Still track-keyed: it joins through the frame candidate, so it sees
    only labels written through track review. Boxes drawn on the canvas
    have no candidate and are invisible here until export is rewritten
    onto frames (ticket 12). Evaluation's class distribution and the
    active-learning queue share the same limitation.
    """
    stmt = (
        select(Annotation, FrameCandidate, Track, Source)
        .join(FrameCandidate, Annotation.frame_candidate_id == FrameCandidate.id)
        .join(Track, FrameCandidate.track_id == Track.id)
        .join(ProcessingRun, Track.run_id == ProcessingRun.id)
        .join(Source, ProcessingRun.source_id == Source.id)
        .join(Frame, Annotation.frame_id == Frame.id)
        .where(
            Source.project_id == project_id,
            Annotation.source == "human",
            Annotation.status == "accepted",
            Frame.status != "rejected",
        )
        .order_by(Track.id)
    )
    return [
        ApprovedItem(annotation=row[0], frame_candidate=row[1], track=row[2], source=row[3])
        for row in db.execute(stmt).all()
    ]
