from typing import Protocol

import numpy as np

from app.ml.types import Detection


class Detector(Protocol):
    """Interface for vehicle detectors. See docs/03_TRD.md: business
    logic must not directly depend on a specific YOLO model version -
    callers only ever depend on this interface."""

    #: The model identifier persisted onto processing_runs.detector_version.
    model_version: str

    #: Native class id -> class name, as reported by the underlying model.
    class_names: dict[int, str]

    def detect(self, frame: np.ndarray) -> list[Detection]: ...
