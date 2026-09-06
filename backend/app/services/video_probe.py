from dataclasses import dataclass
from pathlib import Path

import cv2

from app.core.errors import AppError


class VideoProbeError(AppError):
    code = "video_probe_failed"


@dataclass(frozen=True)
class VideoMetadata:
    fps: float
    width: int
    height: int
    frame_count: int
    duration_ms: int


def probe_video(path: Path) -> VideoMetadata:
    """Read container-level metadata from a video file via OpenCV/FFmpeg."""
    if not path.is_file():
        raise VideoProbeError(f"Source video not found: {path}", code="source_not_found")

    capture = cv2.VideoCapture(str(path))
    try:
        if not capture.isOpened():
            raise VideoProbeError(f"Could not open video file: {path}")

        fps = capture.get(cv2.CAP_PROP_FPS)
        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))

        if fps <= 0 or width <= 0 or height <= 0 or frame_count <= 0:
            raise VideoProbeError(f"Video file has unreadable stream metadata: {path}")

        duration_ms = round(frame_count / fps * 1000)
        return VideoMetadata(fps=fps, width=width, height=height, frame_count=frame_count, duration_ms=duration_ms)
    finally:
        capture.release()
