from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.annotation import Annotation
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

    Annotations reach their project the long way round - through the
    frame candidate, its track, that track's run, and the run's source.
    Written once here so a schema change touches one join rather than
    every service that needs to ask "whose annotation is this".
    """
    return (
        select(Annotation)
        .join(FrameCandidate, Annotation.frame_candidate_id == FrameCandidate.id)
        .join(Track, FrameCandidate.track_id == Track.id)
        .join(ProcessingRun, Track.run_id == ProcessingRun.id)
        .join(Source, ProcessingRun.source_id == Source.id)
        .where(Source.project_id == project_id)
    )


def query_approved_items(db: Session, project_id: str) -> list[ApprovedItem]:
    """Every human-accepted annotation in a project - the dataset-
    eligible set (docs Track Lifecycle step 10: "Approved annotation
    becomes dataset-eligible"). ``hard``/``failed`` tracks are
    preserved in the DB but excluded from export by design - they
    remain available for Phase 8 active learning, just not shipped in
    a training dataset yet.

    A track has at most one human annotation (see docs/HANDOFF.md
    Phase 4 note), so this is automatically deduplicated at the
    vehicle-track level - the "duplicate frames from one vehicle are
    not exported by default" acceptance criterion in docs/01_PRD.md.
    """
    stmt = (
        select(Annotation, FrameCandidate, Track, Source)
        .join(FrameCandidate, Annotation.frame_candidate_id == FrameCandidate.id)
        .join(Track, FrameCandidate.track_id == Track.id)
        .join(ProcessingRun, Track.run_id == ProcessingRun.id)
        .join(Source, ProcessingRun.source_id == Source.id)
        .where(Source.project_id == project_id, Annotation.source == "human", Annotation.status == "accepted")
        .order_by(Track.id)
    )
    return [
        ApprovedItem(annotation=row[0], frame_candidate=row[1], track=row[2], source=row[3])
        for row in db.execute(stmt).all()
    ]
