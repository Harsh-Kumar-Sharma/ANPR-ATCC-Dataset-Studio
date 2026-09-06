import numpy as np
from rapidocr import RapidOCR

from app.ml.types import PlateOcrCandidate

ENGINE_VERSION = "rapidocr"


class RapidOcrEngine:
    """PlateOcrEngine adapter over RapidOCR (runs PaddleOCR's own
    detection/recognition models via ONNX Runtime).

    Swapped in for real PaddleOCR because ``paddlepaddle`` has no
    installable build for this environment's Python version - see
    docs/HANDOFF.md Phase 5. Kept behind PlateOcrEngine so real
    PaddleOCR can replace this without touching any caller.
    """

    def __init__(self) -> None:
        self._engine = RapidOCR()
        self.engine_version = ENGINE_VERSION

    def read_plate(self, vehicle_crop: np.ndarray) -> list[PlateOcrCandidate]:
        result = self._engine(vehicle_crop)
        if result is None or not result.txts:
            return []

        candidates = []
        for box, text, score in zip(result.boxes, result.txts, result.scores):
            xs = box[:, 0]
            ys = box[:, 1]
            candidates.append(
                PlateOcrCandidate(
                    bbox_xyxy=(float(xs.min()), float(ys.min()), float(xs.max()), float(ys.max())),
                    text=text,
                    confidence=float(score),
                )
            )
        return candidates
