from typing import Protocol

from app.ml.types import Detection, TrackedDetection


class Tracker(Protocol):
    """Interface for multi-object trackers. See docs/04_SYSTEM_ARCHITECTURE.md:
    the tracker converts per-frame detector observations into vehicle
    tracks - it never sees pixels, only detections in frame order."""

    def update(self, detections: list[Detection], timestamp_ms: int) -> list[TrackedDetection]: ...
