from dataclasses import dataclass


@dataclass(frozen=True)
class Detection:
    """A single per-frame detector observation. Normalized shape per
    docs/07_ML_CV_PIPELINE.md: bbox, class, confidence - frame timestamp
    is attached by the caller, not the detector itself."""

    bbox_xyxy: tuple[float, float, float, float]
    class_id: int
    confidence: float


@dataclass(frozen=True)
class TrackedDetection:
    """A Detection associated with a persistent track by the tracker."""

    track_id: int
    bbox_xyxy: tuple[float, float, float, float]
    class_id: int
    confidence: float
