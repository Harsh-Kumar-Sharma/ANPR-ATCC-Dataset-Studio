import numpy as np

from app.ml.types import PlateOcrCandidate


class StubOcrEngine:
    """A deterministic PlateOcrEngine for tests - queues one response
    per call, so a test can force a "first frame fails, second frame
    succeeds" sequence without needing real plate imagery."""

    def __init__(self, responses: list[list[PlateOcrCandidate]] | None = None) -> None:
        self.engine_version = "stub-ocr-v1"
        self._responses = responses if responses is not None else [[]]
        self._call_count = 0

    def read_plate(self, vehicle_crop: np.ndarray) -> list[PlateOcrCandidate]:
        response = self._responses[min(self._call_count, len(self._responses) - 1)]
        self._call_count += 1
        return response
