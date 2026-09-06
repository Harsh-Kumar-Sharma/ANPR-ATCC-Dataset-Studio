from functools import lru_cache

from app.core.config import get_settings
from app.ml.bytetrack_tracker import DEFAULT_LOST_TRACK_BUFFER, ByteTrackTracker
from app.ml.detector import Detector
from app.ml.tracker import Tracker
from app.ml.yolo_detector import DEFAULT_MODEL_WEIGHTS, YoloDetector


@lru_cache
def get_default_detector() -> Detector:
    """A YOLO model is expensive to load - reuse one instance across
    requests. Safe to share: detect() holds no per-call mutable state."""
    settings = get_settings()
    weights_path = settings.resolved_model_weights_dir() / DEFAULT_MODEL_WEIGHTS
    return YoloDetector(weights=str(weights_path))


def create_tracker(frame_rate: float) -> Tracker:
    """Trackers accumulate state across calls within a single run, so a
    fresh instance is required per processing run (never shared)."""
    return ByteTrackTracker(frame_rate=frame_rate, lost_track_buffer=DEFAULT_LOST_TRACK_BUFFER)
