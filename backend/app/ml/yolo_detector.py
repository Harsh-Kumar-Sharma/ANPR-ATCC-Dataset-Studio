import numpy as np
from ultralytics import YOLO

from app.core.config import get_settings
from app.ml.device import resolve_device
from app.ml.types import Detection

#: Default weights. Swappable per docs/03_TRD.md - never hard-code a
#: specific model version into calling code, only into this default.
DEFAULT_MODEL_WEIGHTS = "yolo26n.pt"

#: COCO class names relevant to gantry ANPR/ATCC footage. A pretrained
#: COCO model has no notion of the 20 ATCC classes in docs/01_PRD.md -
#: this only filters out non-vehicle detections (people, animals, etc).
#: Fine-grained ATCC classification happens later (human review, then
#: a custom-trained model swapped in behind this same interface).
DEFAULT_VEHICLE_CLASS_NAMES = frozenset({"bicycle", "car", "motorcycle", "bus", "truck"})


class YoloDetector:
    """Detector adapter over an Ultralytics YOLO model."""

    def __init__(
        self,
        weights: str = DEFAULT_MODEL_WEIGHTS,
        confidence_threshold: float = 0.25,
        class_allowlist: frozenset[str] | None = DEFAULT_VEHICLE_CLASS_NAMES,
        device: str | None = None,
        name: str | None = None,
    ) -> None:
        self._model = YOLO(weights)
        self._confidence_threshold = confidence_threshold
        self._class_allowlist = class_allowlist
        # Resolved once here rather than per predict() call: the choice is
        # fixed for the life of the detector, and re-deciding it every frame
        # would re-log it every frame. ``None`` means "ask the config".
        self.device = resolve_device(device if device is not None else get_settings().device)
        # The model id when the caller has one, because that is what
        # gets written onto the run and compared between runs. A path
        # says where the file was that day, not which model ran.
        self.model_version = name or weights
        self.class_names: dict[int, str] = dict(self._model.names)

    def detect(self, frame: np.ndarray) -> list[Detection]:
        results = self._model.predict(frame, conf=self._confidence_threshold, verbose=False, device=self.device)
        detections: list[Detection] = []
        for result in results:
            for box in result.boxes:
                class_id = int(box.cls[0])
                class_name = self.class_names.get(class_id)
                if self._class_allowlist is not None and class_name not in self._class_allowlist:
                    continue
                x1, y1, x2, y2 = (float(v) for v in box.xyxy[0])
                detections.append(
                    Detection(
                        bbox_xyxy=(x1, y1, x2, y2),
                        class_id=class_id,
                        confidence=float(box.conf[0]),
                    )
                )
        return detections
