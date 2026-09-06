import numpy as np
import pytest

from app.services.quality_signals import (
    compute_area_ratio,
    compute_sharpness,
    is_truncated,
    sharpness_to_blur_score,
)


def test_sharp_image_scores_higher_than_blurred_image():
    rng = np.random.default_rng(42)
    sharp = rng.integers(0, 255, (64, 64, 3), dtype=np.uint8)

    # A uniform image has zero Laplacian variance - the "maximally blurred" case.
    blurred = np.full((64, 64, 3), fill_value=128, dtype=np.uint8)

    assert compute_sharpness(sharp) > compute_sharpness(blurred)
    assert compute_sharpness(blurred) == 0.0


def test_sharpness_to_blur_score_is_monotonically_decreasing():
    low = sharpness_to_blur_score(0.0)
    mid = sharpness_to_blur_score(100.0)
    high = sharpness_to_blur_score(10_000.0)

    assert 0.0 < high < mid < low <= 1.0


def test_empty_crop_has_zero_sharpness():
    empty = np.zeros((0, 0, 3), dtype=np.uint8)
    assert compute_sharpness(empty) == 0.0


@pytest.mark.parametrize(
    "bbox,expected",
    [
        ((0, 0, 50, 50), 2500 / (100 * 100)),
        ((0, 0, 100, 100), 1.0),
        ((10, 10, 10, 10), 0.0),  # zero-area bbox
    ],
)
def test_area_ratio(bbox, expected):
    assert compute_area_ratio(bbox, frame_width=100, frame_height=100) == pytest.approx(expected)


def test_area_ratio_is_clamped_to_one_even_if_bbox_exceeds_frame():
    assert compute_area_ratio((0, 0, 200, 200), frame_width=100, frame_height=100) == 1.0


@pytest.mark.parametrize(
    "bbox,expected",
    [
        ((10, 10, 50, 50), False),  # comfortably inside
        ((0, 10, 50, 50), True),  # touches left edge
        ((10, 0, 50, 50), True),  # touches top edge
        ((10, 10, 100, 50), True),  # touches right edge
        ((10, 10, 50, 100), True),  # touches bottom edge
    ],
)
def test_is_truncated(bbox, expected):
    assert is_truncated(bbox, frame_width=100, frame_height=100) == expected
