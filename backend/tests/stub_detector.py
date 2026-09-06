import numpy as np

from app.ml.types import Detection


class StubDetector:
    """A deterministic Detector for tests that don't need real YOLO
    inference - only that something implementing the Detector
    interface fed the tracker consistent per-frame detections."""

    def __init__(self, box_per_frame: dict[int, tuple[float, float, float, float]] | None = None) -> None:
        self.model_version = "stub-detector-v1"
        self.class_names = {0: "car"}
        self._box_per_frame = box_per_frame
        self._call_count = 0

    def detect(self, frame: np.ndarray) -> list[Detection]:
        if self._box_per_frame is not None:
            bbox = self._box_per_frame.get(self._call_count)
            self._call_count += 1
            if bbox is None:
                return []
        else:
            # A vehicle drifting slightly frame to frame, like a real track.
            offset = self._call_count * 2
            self._call_count += 1
            bbox = (10 + offset, 10, 60 + offset, 40)
        return [Detection(bbox_xyxy=bbox, class_id=0, confidence=0.9)]
