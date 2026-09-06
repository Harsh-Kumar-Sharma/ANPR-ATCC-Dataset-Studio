import cv2
import numpy as np

from app.ml.rapidocr_engine import RapidOcrEngine


def _plate_like_image(text: str) -> np.ndarray:
    img = np.full((100, 300, 3), 255, dtype=np.uint8)
    cv2.putText(img, text, (10, 65), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 0, 0), 2)
    return img


def test_reads_text_from_a_plate_like_image():
    engine = RapidOcrEngine()
    candidates = engine.read_plate(_plate_like_image("MH12AB1234"))

    assert len(candidates) >= 1
    best = max(candidates, key=lambda c: c.confidence)
    assert "MH12AB1234" in best.text.upper().replace(" ", "")
    assert best.confidence > 0.5
    assert best.bbox_xyxy[2] > best.bbox_xyxy[0]
    assert best.bbox_xyxy[3] > best.bbox_xyxy[1]


def test_blank_image_returns_no_candidates():
    engine = RapidOcrEngine()
    blank = np.full((100, 300, 3), 255, dtype=np.uint8)
    assert engine.read_plate(blank) == []
