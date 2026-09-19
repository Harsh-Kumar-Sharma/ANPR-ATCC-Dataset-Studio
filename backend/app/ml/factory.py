from collections.abc import Callable
from functools import lru_cache

from app.core.config import get_settings
from app.ml.bytetrack_tracker import DEFAULT_LOST_TRACK_BUFFER, ByteTrackTracker
from app.ml.detector import Detector
from app.ml.plate_ocr import PlateOcrEngine
from app.ml.rapidocr_engine import RapidOcrEngine
from app.ml.tracker import Tracker
from app.ml.models import DEFAULT_MODEL_ID
from app.ml.weights import ensure_weights
from app.ml.yolo_detector import YoloDetector


#: Given a model id, the detector for it.
DetectorProvider = Callable[[str], Detector]


@lru_cache
def get_detector(model_id: str = DEFAULT_MODEL_ID) -> Detector:
    """The detector for one model, loaded once.

    A YOLO model is expensive to load - reuse one instance across
    requests. Safe to share: detect() holds no per-call mutable state.
    Keyed by model so switching to the larger model and back does not
    reload the one already in memory.
    """
    settings = get_settings()
    weights_dir = settings.resolved_model_weights_dir()
    path = ensure_weights(model_id, weights_dir)
    return YoloDetector(weights=str(path), name=model_id, device=settings.device)


def get_detector_provider() -> "DetectorProvider":
    """How an endpoint gets a detector for a named model.

    A callable rather than a detector, because the model is not known
    until the request body is read. Injected so tests can answer with a
    stub instead of loading a real model.
    """
    return get_detector


def get_default_detector() -> Detector:
    """The detector for the default model.

    Kept as its own name because FastAPI dependencies and old callers
    ask for "the detector" without having a model to name.
    """
    return get_detector(DEFAULT_MODEL_ID)


def create_tracker(frame_rate: float) -> Tracker:
    """Trackers accumulate state across calls within a single run, so a
    fresh instance is required per processing run (never shared)."""
    return ByteTrackTracker(frame_rate=frame_rate, lost_track_buffer=DEFAULT_LOST_TRACK_BUFFER)


@lru_cache
def get_default_ocr_engine() -> PlateOcrEngine:
    """An OCR engine is expensive to load - reuse one instance across
    requests, same reasoning as get_default_detector()."""
    return RapidOcrEngine()
