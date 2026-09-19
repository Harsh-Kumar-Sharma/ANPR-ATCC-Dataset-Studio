"""Deleting a frame for good, as distinct from skipping it.

Skipping is a judgement that can be reversed: the frame stays, its
boxes stay, and putting it back restores both. Deleting is for frames
that should never have been offered - blank ones, blurred ones, ones
where the detector fired on nothing. Those are disk being spent on
rubbish, and the decoded image is the part that costs.

The one thing this must not do is rewrite a dataset version that has
already been exported. A version is immutable by design, its manifest
names every frame in it, and something may already have trained on one.
A frame that is in a version is held rather than deleted, and the
message says which version holds it.
"""

from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ConflictError
from app.db.models.annotation import Annotation
from app.db.models.dataset_item import DatasetItem
from app.db.models.dataset_version import DatasetVersion
from app.db.models.frame import Frame
from app.db.models.frame_candidate import FrameCandidate
from app.db.models.ocr_candidate import OcrCandidate
from app.db.models.track import Track
from app.services.cascade import delete_in, ids_in, remove_file


class FrameExportedError(ConflictError):
    """This frame is in a dataset version that has already been exported.

    Deleting it would make that version's manifest describe an image
    that no longer exists, and a version is the record of what a model
    was trained on.
    """

    code = "frame_exported"


@dataclass
class DeletedFrame:
    """What went with the frame."""

    labels: int = 0
    detections: int = 0
    #: Tracks that had no detections left once this frame's were gone.
    emptied_tracks: int = 0
    #: Whether the decoded image was on disk and is now not.
    image_removed: bool = False


def delete_frame(db: Session, frame: Frame) -> tuple[DeletedFrame, Path | None]:
    """Delete a frame's rows. The caller commits, then removes the image.

    Rows first and the file after, the same order the project and
    source deletions settled on: a file left behind can be deleted by
    hand, a row pointing at an image that is gone cannot be reasoned
    about.
    """
    _refuse_if_exported(db, frame)

    annotation_ids = [a for a in db.scalars(select(Annotation.id).where(Annotation.frame_id == frame.id))]
    candidate_ids = [c for c in db.scalars(select(FrameCandidate.id).where(FrameCandidate.frame_id == frame.id))]
    track_ids = sorted(
        {t for t in db.scalars(select(FrameCandidate.track_id).where(FrameCandidate.frame_id == frame.id))}
    )

    # Dataset items should not exist - a frame in a version is refused
    # above - but a version deleted since could have left one, and an
    # orphan pointing at a deleted annotation is the kind of row that
    # only shows up much later in a count that forgot to scope itself.
    delete_in(db, DatasetItem, DatasetItem.annotation_id, annotation_ids)
    delete_in(db, Annotation, Annotation.id, annotation_ids)
    delete_in(db, OcrCandidate, OcrCandidate.frame_candidate_id, candidate_ids)
    delete_in(db, FrameCandidate, FrameCandidate.id, candidate_ids)

    image_path = Path(frame.image_path) if frame.image_path else None
    db.delete(frame)
    db.flush()

    emptied = _delete_emptied_tracks(db, track_ids)

    return (
        DeletedFrame(labels=len(annotation_ids), detections=len(candidate_ids), emptied_tracks=emptied),
        image_path,
    )


def remove_frame_image(image_path: Path | None) -> bool:
    """Delete the decoded image, after the commit.

    A frame kept only as a row costs nothing - the pixels are
    recoverable from the source video. This file is the part that fills
    a disk, and the only reason deleting a frame is worth doing.
    """
    if image_path is None:
        return False
    return remove_file(image_path)


def _refuse_if_exported(db: Session, frame: Frame) -> None:
    version = db.scalar(
        select(DatasetVersion)
        .join(DatasetItem, DatasetItem.dataset_version_id == DatasetVersion.id)
        .join(Annotation, DatasetItem.annotation_id == Annotation.id)
        .where(Annotation.frame_id == frame.id)
        .order_by(DatasetVersion.version)
        .limit(1)
    )
    if version is not None:
        raise FrameExportedError(
            f"This frame is in dataset version v{version.version}, which is immutable. "
            "Delete that version first if you no longer want it."
        )


def _delete_emptied_tracks(db: Session, track_ids: list[str]) -> int:
    """Remove tracks that have no detections left.

    A track is its observations. Once the last one is gone the row is
    an empty entry in the track browser and a row the evaluation report
    still counts, so it goes - but only if it is genuinely empty, since
    one frame of a track is not the track.
    """
    if not track_ids:
        return 0

    still_seen = set(ids_in(db, FrameCandidate.track_id, FrameCandidate.track_id, track_ids))
    emptied = [track_id for track_id in track_ids if track_id not in still_seen]
    if emptied:
        delete_in(db, OcrCandidate, OcrCandidate.track_id, emptied)
        delete_in(db, Track, Track.id, emptied)
        db.flush()
    return len(emptied)
