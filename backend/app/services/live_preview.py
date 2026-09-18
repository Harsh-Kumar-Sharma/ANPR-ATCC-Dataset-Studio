import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

import cv2
import numpy as np

_MAX_PREVIEW_WIDTH = 960
_JPEG_QUALITY = 75
# BGR. Picked to stay distinguishable against asphalt, lane paint and
# night-time sodium lighting.
_PALETTE = [(80, 220, 60), (0, 170, 255), (255, 140, 30), (200, 80, 255), (60, 220, 230), (60, 90, 255)]


@dataclass(frozen=True)
class PreviewBox:
    bbox_xyxy: tuple[float, float, float, float]
    label: str
    track_id: int


@dataclass(frozen=True)
class PreviewFrame:
    jpeg: bytes
    sequence: int
    width: int
    height: int


def render_preview_jpeg(frame: np.ndarray, boxes: list[PreviewBox]) -> tuple[bytes, int, int]:
    """Draw tracked boxes onto a copy of the frame and JPEG-encode it.

    Boxes are burned into the image rather than overlaid by the UI: a
    live frame and its detections would otherwise travel as two separate
    responses and visibly drift apart. Wide frames are downscaled first,
    since a 1080p JPEG several times a second is wasted bandwidth and CPU
    for a preview pane.
    """
    height, width = frame.shape[:2]
    scale = min(1.0, _MAX_PREVIEW_WIDTH / width)
    if scale < 1.0:
        canvas = cv2.resize(frame, (round(width * scale), round(height * scale)), interpolation=cv2.INTER_AREA)
    else:
        canvas = frame.copy()

    thickness = max(2, round(canvas.shape[1] / 400))
    font_scale = max(0.45, canvas.shape[1] / 1600)

    for box in boxes:
        color = _PALETTE[box.track_id % len(_PALETTE)]
        x1, y1, x2, y2 = (round(v * scale) for v in box.bbox_xyxy)
        cv2.rectangle(canvas, (x1, y1), (x2, y2), color, thickness)

        (text_width, text_height), baseline = cv2.getTextSize(box.label, cv2.FONT_HERSHEY_SIMPLEX, font_scale, 1)
        label_bottom = max(y1, text_height + baseline + 4)
        cv2.rectangle(
            canvas, (x1, label_bottom - text_height - baseline - 4), (x1 + text_width + 6, label_bottom), color, cv2.FILLED
        )
        cv2.putText(
            canvas,
            box.label,
            (x1 + 3, label_bottom - baseline - 2),
            cv2.FONT_HERSHEY_SIMPLEX,
            font_scale,
            (0, 0, 0),
            1,
            cv2.LINE_AA,
        )

    ok, encoded = cv2.imencode(".jpg", canvas, [cv2.IMWRITE_JPEG_QUALITY, _JPEG_QUALITY])
    if not ok:
        raise RuntimeError("could not JPEG-encode preview frame")
    return encoded.tobytes(), canvas.shape[1], canvas.shape[0]


class LivePreview:
    """The most recent annotated frame of a running session, held in
    memory only.

    Never touches the database: the UI polls this several times a second,
    and during a run every DB write already contends for SQLite's single
    write lock (see docs/HANDOFF.md).
    """

    def __init__(self, min_interval_seconds: float = 0.12, clock: Callable[[], float] = time.monotonic) -> None:
        self._min_interval = min_interval_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._latest: PreviewFrame | None = None
        self._last_render: float | None = None

    def update(self, frame: np.ndarray, boxes: list[PreviewBox]) -> bool:
        """Render and publish a frame, unless one was published less than
        ``min_interval_seconds`` ago. Rendering every processed frame
        would spend CPU the detector needs on frames nobody can see."""
        now = self._clock()
        if self._last_render is not None and now - self._last_render < self._min_interval:
            return False

        jpeg, width, height = render_preview_jpeg(frame, boxes)
        with self._lock:
            sequence = self._latest.sequence + 1 if self._latest else 1
            self._latest = PreviewFrame(jpeg=jpeg, sequence=sequence, width=width, height=height)
            self._last_render = now
        return True

    def latest(self) -> PreviewFrame | None:
        with self._lock:
            return self._latest
