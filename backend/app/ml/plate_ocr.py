from typing import Protocol

import numpy as np

from app.ml.types import PlateOcrCandidate


class PlateOcrEngine(Protocol):
    """Interface for plate localization + reading. See docs/03_TRD.md:
    business logic must not directly depend on a specific OCR engine
    version - callers only ever depend on this interface.

    docs/02_IMPLEMENTATION_PLAN.md Phase 5 lists "plate candidate
    interface" and "PaddleOCR adapter" as separate build items; here
    they're the same interface because the underlying engines locate
    and read text in a single pass - splitting that into two fake
    interfaces would add nothing. A "plate candidate" is real as a
    concept (each returned PlateOcrCandidate is one), just not as a
    separate call.
    """

    #: The OCR engine identifier - not persisted per-run today (no
    #: processing_runs-style table for OCR yet), but kept for parity
    #: with Detector and for logging.
    engine_version: str

    def read_plate(self, vehicle_crop: np.ndarray) -> list[PlateOcrCandidate]: ...
