from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2

from app.core.errors import AppError


@dataclass(frozen=True)
class SampledFrame:
    frame_index: int
    timestamp_ms: int


def sample_frame_timestamps(frame_count: int, native_fps: float, target_fps: float) -> list[SampledFrame]:
    """Compute which frame indices to sample and their source timestamps.

    Sampling is driven by frame index against the source's native FPS,
    never by wall-clock or decode-time behavior, so the same
    (frame_count, native_fps, target_fps) always produces the exact
    same timestamps - the "reproducible source timestamps" requirement
    in docs/01_PRD.md (FR-03).
    """
    if frame_count <= 0:
        raise ValueError("frame_count must be positive")
    if native_fps <= 0:
        raise ValueError("native_fps must be positive")
    if target_fps <= 0:
        raise ValueError("target_fps must be positive")

    interval = max(1, round(native_fps / target_fps))
    return [
        SampledFrame(frame_index=i, timestamp_ms=round(i / native_fps * 1000))
        for i in range(0, frame_count, interval)
    ]


class FrameDecodeError(AppError):
    code = "frame_decode_failed"


def decode_sampled_frames(video_path: Path, sampled_frames: list[SampledFrame]) -> Iterator[tuple[SampledFrame, Any]]:
    """Decode the given frame indices from a video file, in order.

    Seeks by frame index (not by millisecond position) to stay
    consistent with how ``sample_frame_timestamps`` chose the indices.
    """
    capture = cv2.VideoCapture(str(video_path))
    try:
        if not capture.isOpened():
            raise FrameDecodeError(f"Could not open video file: {video_path}")

        for sampled in sampled_frames:
            capture.set(cv2.CAP_PROP_POS_FRAMES, sampled.frame_index)
            ok, image = capture.read()
            if not ok:
                raise FrameDecodeError(f"Could not decode frame {sampled.frame_index} of {video_path}")
            yield sampled, image
    finally:
        capture.release()
