from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models.frame_candidate import FrameCandidate
from app.db.models.processing_run import ProcessingRun
from app.db.models.track import Track


@dataclass
class DetectionBox:
    track_id: str
    tracker_track_id: int
    bbox: list[float]
    detector_class: str
    confidence: float
    bucket: str | None
    review_status: str


@dataclass
class DetectionFrame:
    frame_index: int
    timestamp_ms: int
    boxes: list[DetectionBox] = field(default_factory=list)


def list_runs_with_track_counts(db: Session, source_id: str) -> list[tuple[ProcessingRun, int]]:
    """Every processing run of a source with its track count, newest first."""
    stmt = (
        select(ProcessingRun, func.count(Track.id))
        .outerjoin(Track, Track.run_id == ProcessingRun.id)
        .where(ProcessingRun.source_id == source_id)
        .group_by(ProcessingRun.id)
        .order_by(ProcessingRun.started_at.desc())
    )
    return [(run, count) for run, count in db.execute(stmt).all()]


def detections_by_frame(db: Session, run_id: str) -> list[DetectionFrame]:
    """Every persisted tracked detection of one run, grouped by frame and
    ordered by time.

    Only frames the tracker kept a vehicle on appear, and the sampler ran
    at the run's target FPS - so consecutive entries are usually one
    sampling interval apart, not one native video frame apart. Bridging
    those gaps during playback is the player's job.
    """
    stmt = (
        select(FrameCandidate, Track)
        .join(Track, FrameCandidate.track_id == Track.id)
        .where(Track.run_id == run_id)
        .order_by(FrameCandidate.frame_index, Track.tracker_track_id)
    )

    frames: dict[int, DetectionFrame] = {}
    for candidate, track in db.execute(stmt).all():
        frame = frames.setdefault(
            candidate.frame_index, DetectionFrame(frame_index=candidate.frame_index, timestamp_ms=candidate.timestamp_ms)
        )
        frame.boxes.append(
            DetectionBox(
                track_id=track.id,
                tracker_track_id=track.tracker_track_id,
                bbox=list(candidate.bbox_json),
                detector_class=candidate.detector_class,
                confidence=candidate.detector_confidence,
                bucket=track.bucket,
                review_status=track.review_status,
            )
        )
    return list(frames.values())
