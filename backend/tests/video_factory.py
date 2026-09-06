from pathlib import Path

import cv2
import numpy as np


def create_synthetic_video(path: Path, frame_count: int, fps: float, width: int = 64, height: int = 48) -> Path:
    """Write a tiny synthetic video so tests don't need real footage fixtures.

    Each frame is a solid color that increments, so decoded frames are
    trivially distinguishable by content if a test ever needs that.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(path), fourcc, fps, (width, height))
    try:
        for i in range(frame_count):
            frame = np.full((height, width, 3), fill_value=i % 256, dtype=np.uint8)
            writer.write(frame)
    finally:
        writer.release()
    return path
