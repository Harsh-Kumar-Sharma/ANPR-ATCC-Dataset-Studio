from pathlib import Path

import cv2
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.db.models.frame import Frame
from app.db.models.source import Source
from app.services.frame_sampler import SampledFrame, decode_sampled_frames


class FrameMaterializationError(AppError):
    code = "frame_materialization_failed"


def frames_root(workspace_path: Path) -> Path:
    return workspace_path / "derived" / "frames"


def get_or_create_frame(
    db: Session, source_id: str, frame_index: int, timestamp_ms: int, width: int, height: int
) -> Frame:
    """Record the identity of a sampled full frame without writing any
    pixels. Cheap by design - see ``Frame.image_path``."""
    existing = db.scalar(select(Frame).where(Frame.source_id == source_id, Frame.frame_index == frame_index))
    if existing is not None:
        return existing

    frame = Frame(
        source_id=source_id,
        frame_index=frame_index,
        timestamp_ms=timestamp_ms,
        width=width,
        height=height,
    )
    db.add(frame)
    db.flush()
    return frame


def materialize_frames(
    db: Session, frames: list[Frame], source: Source, workspace_path: Path
) -> dict[str, Path]:
    """Ensure the given frames exist as image files on disk, decoding
    whatever is missing from the source video, and return their paths
    keyed by frame id.

    Decodes in one pass with a single ``VideoCapture``, in ascending
    frame order, because seeking backwards in a compressed stream is
    expensive - exporting hundreds of frames one-open-per-frame would
    be dramatically slower than one ordered pass.
    """
    resolved: dict[str, Path] = {}
    pending: list[Frame] = []

    for frame in frames:
        cached = _cached_path(frame)
        if cached is not None:
            resolved[frame.id] = cached
        else:
            pending.append(frame)

    if not pending:
        return resolved

    video_path = Path(source.path_or_uri)
    if not video_path.is_file():
        raise FrameMaterializationError(
            f"Cannot recover full frames: source video is missing at {video_path}. "
            "Frames without a stored image depend on the imported source file still being present."
        )

    destination_dir = frames_root(workspace_path) / source.id
    destination_dir.mkdir(parents=True, exist_ok=True)

    pending.sort(key=lambda f: f.frame_index)
    by_index = {f.frame_index: f for f in pending}
    sampled = [SampledFrame(frame_index=f.frame_index, timestamp_ms=f.timestamp_ms) for f in pending]

    for sampled_frame, image in decode_sampled_frames(video_path, sampled):
        frame = by_index[sampled_frame.frame_index]
        image_path = destination_dir / f"frame_{frame.frame_index:06d}.jpg"
        if not cv2.imwrite(str(image_path), image):
            raise FrameMaterializationError(f"Could not write full frame image to {image_path}")

        height, width = image.shape[:2]
        frame.image_path = str(image_path)
        frame.width = width
        frame.height = height
        resolved[frame.id] = image_path

    missing = [f.frame_index for f in pending if f.id not in resolved]
    if missing:
        raise FrameMaterializationError(f"Could not decode frame(s) {missing} from {video_path}")

    db.flush()
    return resolved


def _cached_path(frame: Frame) -> Path | None:
    if not frame.image_path:
        return None
    path = Path(frame.image_path)
    # A recorded path that no longer exists (workspace moved, file
    # cleaned up) is treated as "not materialized" rather than an error,
    # since the frame is still recoverable from the source video.
    return path if path.is_file() else None
