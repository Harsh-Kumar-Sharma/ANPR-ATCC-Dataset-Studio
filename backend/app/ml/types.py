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
    #: False when the tracker never managed to follow this detection
    #: across frames and it stands on its own. A real observation
    #: either way - see ``ByteTrackTracker``.
    confirmed: bool = True


@dataclass(frozen=True)
class PlateOcrCandidate:
    """A located-and-read plate candidate within a vehicle crop.
    ``bbox_xyxy`` is relative to the vehicle crop image, not the
    source frame - see docs/07_ML_CV_PIPELINE.md OCR steps 2-3."""

    bbox_xyxy: tuple[float, float, float, float]
    text: str
    confidence: float
