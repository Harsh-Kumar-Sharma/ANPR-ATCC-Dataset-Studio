"""Keeping the frames you labelled and deleting the rest.

A live session can keep every tenth frame it captures, which is what
makes a session useful when detection finds nothing. But most of those
frames are a road with nothing on it, and they are the expensive part:
one 1080p JPEG each, on a disk with single-digit gigabytes free.

So: label the ones worth labelling, then sweep. What you labelled
stays. Everything else goes for good - the row and the image.
"""

import logging
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.frame import Frame
from app.db.models.source import Source
from app.services.cascade import file_size
from app.services.frame_deletion import FrameExportedError, delete_frame, remove_frame_image

logger = logging.getLogger(__name__)

#: A frame with boxes on it is one someone worked on. It stays,
#: whatever else is true of it.
KEPT_STATUSES = ("labeled",)


@dataclass
class Sweep:
    """What a sweep would do, or did."""

    #: Frames that stay because they were labelled.
    kept: int = 0
    #: Frames deleted, or that would be.
    deleted: int = 0
    #: Frames held back because a dataset version already names them.
    #: Not an error: a version is the record of what a model was
    #: trained on, and it has to keep describing real files.
    held: int = 0
    #: Bytes of decoded images, freed or to be freed.
    bytes_freed: int = 0


def _frames_of(db: Session, project_id: str, source_id: str | None) -> list[Frame]:
    stmt = select(Frame).join(Source, Frame.source_id == Source.id).where(Source.project_id == project_id)
    if source_id is not None:
        stmt = stmt.where(Frame.source_id == source_id)
    return list(db.scalars(stmt.order_by(Frame.source_id, Frame.frame_index)))


def _image_size(frame: Frame) -> int:
    return file_size(Path(frame.image_path)) if frame.image_path else 0


def preview(db: Session, project_id: str, source_id: str | None = None) -> Sweep:
    """What a sweep would delete, without deleting it.

    Its own call because a confirmation that cannot say what is about
    to go is not a confirmation - the same reasoning as deleting a
    project or a source.
    """
    counted = Sweep()
    for frame in _frames_of(db, project_id, source_id):
        if frame.status in KEPT_STATUSES:
            counted.kept += 1
            continue
        counted.deleted += 1
        counted.bytes_freed += _image_size(frame)
    return counted


def sweep(db: Session, project_id: str, source_id: str | None = None) -> tuple[Sweep, list[Path]]:
    """Delete every frame of this project, or this source, that nobody
    labelled.

    Returns what it did and the images to remove. The caller commits
    first and removes them after - the order every deletion in this
    app settled on: a file left behind can be deleted later, a row
    pointing at a file that has gone is a broken image in the queue.
    """
    done = Sweep()
    images: list[Path] = []

    for frame in _frames_of(db, project_id, source_id):
        if frame.status in KEPT_STATUSES:
            done.kept += 1
            continue

        size = _image_size(frame)
        try:
            _, image_path = delete_frame(db, frame)
        except FrameExportedError:
            # Held, not failed. One exported frame must not stop the
            # sweep from clearing the other nine hundred.
            done.held += 1
            continue

        done.deleted += 1
        done.bytes_freed += size
        if image_path is not None:
            images.append(image_path)

    db.flush()
    return done, images


def remove_swept_images(paths: list[Path]) -> int:
    """Delete the images a committed sweep left behind."""
    return sum(1 for path in paths if remove_frame_image(path))
