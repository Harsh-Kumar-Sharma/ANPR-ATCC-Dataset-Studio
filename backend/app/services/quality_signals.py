import cv2
import numpy as np


def compute_sharpness(crop: np.ndarray) -> float:
    """Variance of the Laplacian - the standard cheap sharpness proxy.
    Higher means sharper (less blurred)."""
    if crop.size == 0:
        return 0.0
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


#: Maps raw sharpness variance to blur_score. Uncalibrated against real
#: gantry footage (see docs/HANDOFF.md known issues) - a reasonable
#: starting point, not a tuned threshold.
DEFAULT_SHARPNESS_CALIBRATION_K = 100.0


def sharpness_to_blur_score(sharpness: float, calibration_k: float = DEFAULT_SHARPNESS_CALIBRATION_K) -> float:
    """Normalize unbounded sharpness variance into a (0, 1] blurriness
    score: closer to 1 is more blurred, closer to 0 is sharp."""
    return 1.0 / (1.0 + sharpness / calibration_k)


def compute_area_ratio(bbox_xyxy: tuple[float, float, float, float], frame_width: int, frame_height: int) -> float:
    """Fraction of the full frame the vehicle bounding box occupies."""
    x1, y1, x2, y2 = bbox_xyxy
    bbox_area = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    frame_area = frame_width * frame_height
    if frame_area <= 0:
        return 0.0
    return min(1.0, bbox_area / frame_area)


def is_truncated(
    bbox_xyxy: tuple[float, float, float, float], frame_width: int, frame_height: int, epsilon: float = 2.0
) -> bool:
    """Whether the bbox touches the frame boundary - the vehicle is
    likely only partially visible, not fully within frame."""
    x1, y1, x2, y2 = bbox_xyxy
    return x1 <= epsilon or y1 <= epsilon or x2 >= frame_width - epsilon or y2 >= frame_height - epsilon
