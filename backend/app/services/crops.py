"""A vehicle crop, cut out of its frame when something wants to look at
it.

Detection used to write a JPEG for every observation as it went. At two
vehicles a frame that is one file per vehicle per frame, and a full-rate
pass over a 90,000-frame video is gigabytes of them before anything has
been reviewed. The pixels are still in the source video, so they are cut
out again on demand instead - the same bargain full frames already make.
"""

from pathlib import Path

import cv2

from app.core.errors import AppError, NotFoundError
from app.db.models.frame import Frame
from app.db.models.frame_candidate import FrameCandidate
from app.db.models.source import Source
from app.services.frame_materializer import materialize_frames
from sqlalchemy.orm import Session

#: Good enough to review a vehicle on, small enough not to matter.
JPEG_QUALITY = 90


class CropUnavailableError(AppError):
    code = "crop_unavailable"


def crop_jpeg(db: Session, candidate: FrameCandidate) -> bytes:
    """This observation's pixels, as JPEG bytes."""
    stored = _stored_crop(candidate)
    if stored is not None:
        return stored.read_bytes()

    ok, encoded = cv2.imencode(".jpg", crop_array(db, candidate), [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY])
    if not ok:
        raise CropUnavailableError("Could not encode the crop.")
    return encoded.tobytes()


def crop_array(db: Session, candidate: FrameCandidate):
    """This observation's pixels, decoded.

    A stored crop is used when there is one - a live capture writes
    them, because its frames cannot be decoded a second time.
    Otherwise the full frame is materialised (itself cached) and the
    box is cut out of it.
    """
    stored = _stored_crop(candidate)
    if stored is not None:
        image = cv2.imread(str(stored))
        if image is None:
            raise CropUnavailableError(f"Could not read the stored crop at {stored}")
        return image

    frame = db.get(Frame, candidate.frame_id)
    if frame is None:
        raise NotFoundError(f"The frame this crop belongs to is gone: {candidate.frame_id}")
    source = db.get(Source, frame.source_id)
    if source is None:
        raise NotFoundError(f"The source this crop belongs to is gone: {frame.source_id}")

    from app.services.frames import project_of

    project = project_of(db, frame)
    image_path = materialize_frames(db, [frame], source, Path(project.workspace_path))[frame.id]
    image = cv2.imread(str(image_path))
    if image is None:
        raise CropUnavailableError(f"Could not read the frame this crop comes from: {image_path}")

    cut = _cut(image, tuple(candidate.bbox_json))
    if cut is None:
        raise CropUnavailableError(
            f"The box for this observation falls outside its frame: {candidate.bbox_json}"
        )
    return cut


def _stored_crop(candidate: FrameCandidate) -> Path | None:
    """A crop written at capture time, if it is still there.

    A recorded path that has gone - workspace moved, space reclaimed -
    is treated as "not stored" rather than an error, the same way a
    materialised frame is: the pixels may still be recoverable.
    """
    if not candidate.image_path:
        return None
    path = Path(candidate.image_path)
    return path if path.is_file() else None


def _cut(image, bbox_xyxy: tuple[float, float, float, float]):
    height, width = image.shape[:2]
    x1, y1, x2, y2 = (int(round(v)) for v in bbox_xyxy)
    x1, x2 = max(0, min(x1, width)), max(0, min(x2, width))
    y1, y2 = max(0, min(y1, height)), max(0, min(y2, height))
    if x2 <= x1 or y2 <= y1:
        return None
    return image[y1:y2, x1:x2]
