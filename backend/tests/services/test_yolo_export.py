import pytest

from app.services.yolo_export import bbox_relative_to_crop, format_yolo_label_line, normalize_yolo_bbox


def test_bbox_relative_to_crop_with_no_edit_spans_the_whole_crop():
    original = (10, 20, 60, 70)  # 50x50 crop
    local = bbox_relative_to_crop(original, original)
    assert local == (0, 0, 50, 50)


def test_bbox_relative_to_crop_with_a_tightened_edit():
    original_frame_bbox = (10, 20, 60, 70)
    edited = (20, 25, 50, 65)
    local = bbox_relative_to_crop(edited, original_frame_bbox)
    assert local == (10, 5, 40, 45)


def test_normalize_yolo_bbox_full_crop_is_centered_unit_box():
    normalized = normalize_yolo_bbox((0, 0, 50, 50), image_width=50, image_height=50)
    assert normalized == pytest.approx((0.5, 0.5, 1.0, 1.0))


def test_normalize_yolo_bbox_quarter_region():
    normalized = normalize_yolo_bbox((0, 0, 25, 25), image_width=50, image_height=50)
    assert normalized == pytest.approx((0.25, 0.25, 0.5, 0.5))


def test_normalize_yolo_bbox_clamps_out_of_bounds_edit():
    # An edited bbox that overshoots the crop still yields a valid [0,1] label.
    normalized = normalize_yolo_bbox((-10, -10, 200, 200), image_width=50, image_height=50)
    assert all(0.0 <= v <= 1.0 for v in normalized)
    assert normalized == pytest.approx((0.5, 0.5, 1.0, 1.0))


def test_format_yolo_label_line():
    line = format_yolo_label_line(3, (0.5, 0.5, 1.0, 1.0))
    assert line == "3 0.500000 0.500000 1.000000 1.000000"
    parts = line.split()
    assert len(parts) == 5
    assert parts[0] == "3"
