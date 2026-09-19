"""Small pictures of frames, for looking at two hundred at once.

The labelling queue is a list of "frame 0, frame 7, frame 14" - which
says nothing about which of them has a vehicle in it. Looking through
two hundred frames one click at a time is the slow way to find the
four that are worth labelling.

A contact sheet needs pictures, and it must not cost what full frames
cost. A 1080p JPEG is a quarter of a megabyte and, for a video
source, decoding one writes it to disk - two hundred of those is the
disk problem this project has already had once. So a thumbnail is
made at a few tens of kilobytes, cached beside the frames, and the
full-size decode is left for the frame the user actually opens.
"""

import logging
from pathlib import Path

import cv2
import numpy as np
from sqlalchemy.orm import Session

from app.core.errors import AppError, NotFoundError
from app.db.models.frame import Frame
from app.db.models.source import Source
from app.services.frame_sampler import SampledFrame, decode_sampled_frames

logger = logging.getLogger(__name__)

#: Wide enough to see a vehicle and whether it has a plate on it,
#: small enough that two hundred of them are a few megabytes.
THUMBNAIL_WIDTH = 320

#: Lower than a frame meant for labelling. Nobody draws a box on a
#: thumbnail.
JPEG_QUALITY = 72


class ThumbnailUnavailableError(AppError):
    code = "thumbnail_unavailable"


def thumbs_root(workspace_path: Path) -> Path:
    return workspace_path / "derived" / "thumbs"


def thumbnail_path(workspace_path: Path, frame: Frame) -> Path:
    return thumbs_root(workspace_path) / frame.source_id / f"frame_{frame.frame_index:06d}.jpg"


def thumbnail_jpeg(db: Session, frame: Frame, workspace_path: Path) -> bytes:
    """A small JPEG of this frame, made once and kept.

    Deliberately does not materialise the full frame. Opening a grid
    of two hundred frames from a video would otherwise decode two
    hundred 1080p images to disk, which is the cost this whole
    approach exists to avoid.
    """
    cached = thumbnail_path(workspace_path, frame)
    if cached.is_file():
        return cached.read_bytes()

    image = _read_frame(db, frame)
    thumbnail = _shrink(image)

    ok, encoded = cv2.imencode(".jpg", thumbnail, [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY])
    if not ok:
        raise ThumbnailUnavailableError(f"Could not encode a thumbnail for frame {frame.frame_index}")

    try:
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_bytes(encoded.tobytes())
    except OSError:
        # Worth serving anyway. A thumbnail that cannot be cached is
        # slow, not broken.
        logger.warning("Could not cache the thumbnail at %s", cached, exc_info=True)

    return encoded.tobytes()


def _read_frame(db: Session, frame: Frame) -> np.ndarray:
    """The frame's pixels, from wherever they are.

    A stored image when there is one - a live capture writes them.
    Otherwise decoded from the source video into memory and left
    there: the point is not to write the full-size file.
    """
    if frame.image_path and Path(frame.image_path).is_file():
        image = cv2.imread(frame.image_path)
        if image is not None:
            return image

    source = db.get(Source, frame.source_id)
    if source is None:
        raise NotFoundError(f"The source this frame belongs to is gone: {frame.source_id}")

    video = Path(source.path_or_uri)
    if source.type != "video" or not video.is_file():
        raise ThumbnailUnavailableError(
            f"There is no picture for frame {frame.frame_index}: it was captured live and its image "
            "is no longer on disk."
        )

    sampled = [SampledFrame(frame_index=frame.frame_index, timestamp_ms=frame.timestamp_ms)]
    for _, image in decode_sampled_frames(video, sampled):
        return image
    raise ThumbnailUnavailableError(f"Could not decode frame {frame.frame_index} from {video}")


def _shrink(image: np.ndarray) -> np.ndarray:
    height, width = image.shape[:2]
    if width <= THUMBNAIL_WIDTH:
        return image
    scale = THUMBNAIL_WIDTH / width
    # INTER_AREA is the one that does not alias when shrinking, which
    # matters when what you are looking for is a small bright plate.
    return cv2.resize(image, (THUMBNAIL_WIDTH, max(1, round(height * scale))), interpolation=cv2.INTER_AREA)
