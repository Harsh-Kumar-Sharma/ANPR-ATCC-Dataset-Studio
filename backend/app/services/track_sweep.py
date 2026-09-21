"""Clearing out the detections nobody wanted.

Reviewing is: look at what the model found, accept the ones worth
keeping, and then be left with a list of a hundred you do not want.
Deleting those one at a time is not reviewing, it is tidying.

The Label tab already has this for frames. This is the same idea on
the review side, where the unit is a detection rather than a frame.
"""

import logging
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models.annotation import Annotation
from app.db.models.frame import Frame
from app.db.models.frame_candidate import FrameCandidate
from app.db.models.ocr_candidate import OcrCandidate
from app.db.models.processing_run import ProcessingRun
from app.db.models.source import Source
from app.db.models.track import Track
from app.services.cascade import delete_in, file_size
from app.services.frame_deletion import FrameExportedError, delete_frame, remove_frame_image

logger = logging.getLogger(__name__)

#: Review decisions that mean "I looked at this and want it kept".
#: "hard" is deliberate: someone marked it difficult, which is a
#: reason to keep it for training, not to throw it away.
KEPT_DECISIONS = ("accepted", "hard")


@dataclass
class TrackSweep:
    """What clearing out the unwanted detections would take, or took."""

    #: Detections that stay because a human accepted or flagged them.
    kept: int = 0
    #: Detections deleted, or that would be.
    deleted: int = 0
    #: Frames that went with them, having nothing left on them.
    frames_deleted: int = 0
    #: Held back because a dataset version already names them.
    held: int = 0
    bytes_freed: int = 0


def _tracks_of(db: Session, project_id: str, run_id: str | None) -> list[Track]:
    stmt = (
        select(Track)
        .join(ProcessingRun, Track.run_id == ProcessingRun.id)
        .join(Source, ProcessingRun.source_id == Source.id)
        .where(Source.project_id == project_id)
    )
    if run_id is not None:
        stmt = stmt.where(Track.run_id == run_id)
    return list(db.scalars(stmt))


def _frames_of(db: Session, track: Track) -> list[str]:
    return list(db.scalars(select(FrameCandidate.frame_id).where(FrameCandidate.track_id == track.id)))


def _worth_keeping(db: Session, frame: Frame, kept_track_ids: set[str]) -> bool:
    """Is there a reason to keep this frame beyond the detection on it?

    A frame someone labelled by hand, or that carries an accepted
    detection, is not rubbish just because one other detection on it
    was never reviewed.
    """
    if frame.status == "labeled":
        return True
    if db.scalar(select(func.count(Annotation.id)).where(Annotation.frame_id == frame.id)):
        return True
    on_frame = set(db.scalars(select(FrameCandidate.track_id).where(FrameCandidate.frame_id == frame.id)))
    return bool(on_frame & kept_track_ids)


def preview(db: Session, project_id: str, run_id: str | None = None) -> TrackSweep:
    """What a sweep would take, without taking it.

    Its own call because a confirmation that cannot say what is about
    to go is not a confirmation.
    """
    counted = TrackSweep()
    tracks = _tracks_of(db, project_id, run_id)
    kept_ids = {t.id for t in tracks if t.review_status in KEPT_DECISIONS}

    doomed_frames: set[str] = set()
    for track in tracks:
        if track.id in kept_ids:
            counted.kept += 1
            continue
        counted.deleted += 1
        for frame_id in _frames_of(db, track):
            if frame_id is not None:
                doomed_frames.add(frame_id)

    for frame_id in doomed_frames:
        frame = db.get(Frame, frame_id)
        if frame is None or _worth_keeping(db, frame, kept_ids):
            continue
        counted.frames_deleted += 1
        if frame.image_path:
            counted.bytes_freed += file_size(Path(frame.image_path))
    return counted


def sweep(db: Session, project_id: str, run_id: str | None = None) -> tuple[TrackSweep, list[Path]]:
    """Delete every detection nobody accepted, and the frames left
    holding nothing.

    Returns what it did and the images to remove; the caller commits
    first and removes them after, the order every deletion in this
    app settled on.
    """
    done = TrackSweep()
    images: list[Path] = []

    tracks = _tracks_of(db, project_id, run_id)
    kept_ids = {t.id for t in tracks if t.review_status in KEPT_DECISIONS}
    done.kept = len(kept_ids)

    touched: set[str] = set()
    for track in tracks:
        if track.id in kept_ids:
            continue
        for frame_id in _frames_of(db, track):
            if frame_id is not None:
                touched.add(frame_id)
        if _remove_track(db, track):
            done.deleted += 1
        else:
            done.held += 1

    # Frames whose last reason to exist has just gone. Through
    # delete_frame so they get the same refusal an exported frame
    # gets anywhere else, and so the image is removed the same way.
    for frame_id in touched:
        frame = db.get(Frame, frame_id)
        if frame is None or _worth_keeping(db, frame, kept_ids):
            continue
        if db.scalar(select(func.count(FrameCandidate.id)).where(FrameCandidate.frame_id == frame.id)):
            continue
        size = file_size(Path(frame.image_path)) if frame.image_path else 0
        try:
            _, image = delete_frame(db, frame)
        except FrameExportedError:
            done.held += 1
            continue
        done.frames_deleted += 1
        done.bytes_freed += size
        if image is not None:
            images.append(image)

    db.flush()
    return done, images


def _remove_track(db: Session, track: Track) -> bool:
    """Delete one track's rows. False when a dataset version holds it.

    A version is the record of what a model was trained on: an
    annotation it names has to keep existing, so the track carrying
    it stays too.
    """
    candidate_ids = list(db.scalars(select(FrameCandidate.id).where(FrameCandidate.track_id == track.id)))
    annotation_ids = list(
        db.scalars(select(Annotation.id).where(Annotation.frame_candidate_id.in_(candidate_ids)))
    ) if candidate_ids else []

    if annotation_ids and _any_exported(db, annotation_ids):
        return False

    delete_in(db, Annotation, Annotation.id, annotation_ids)
    delete_in(db, OcrCandidate, OcrCandidate.frame_candidate_id, candidate_ids)
    delete_in(db, FrameCandidate, FrameCandidate.id, candidate_ids)
    db.delete(track)
    return True


def _any_exported(db: Session, annotation_ids: list[str]) -> bool:
    from app.db.models.dataset_item import DatasetItem

    return bool(
        db.scalar(
            select(func.count(DatasetItem.id)).where(DatasetItem.annotation_id.in_(annotation_ids))
        )
    )


def remove_swept_images(paths: list[Path]) -> int:
    """Delete the images a committed sweep left behind."""
    return sum(1 for path in paths if remove_frame_image(path))
