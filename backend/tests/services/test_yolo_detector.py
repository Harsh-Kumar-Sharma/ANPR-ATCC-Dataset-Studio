import numpy as np

from app.core.config import get_settings
from app.ml.yolo_detector import DEFAULT_MODEL_WEIGHTS, YoloDetector


def test_detector_loads_and_exposes_version_and_classes():
    weights_path = get_settings().resolved_model_weights_dir() / DEFAULT_MODEL_WEIGHTS
    detector = YoloDetector(weights=str(weights_path))

    assert detector.model_version == str(weights_path)
    assert "car" in detector.class_names.values()


def test_detect_returns_a_list_without_error_on_a_blank_frame():
    weights_path = get_settings().resolved_model_weights_dir() / DEFAULT_MODEL_WEIGHTS
    detector = YoloDetector(weights=str(weights_path))

    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    detections = detector.detect(frame)

    assert isinstance(detections, list)


def test_detector_records_the_device_it_resolved():
    """The detector must not leave device choice to an Ultralytics default -
    that is how this project silently ran on CPU. See app/ml/device.py."""
    weights_path = get_settings().resolved_model_weights_dir() / DEFAULT_MODEL_WEIGHTS
    detector = YoloDetector(weights=str(weights_path), device="cpu")

    assert detector.device == "cpu"
