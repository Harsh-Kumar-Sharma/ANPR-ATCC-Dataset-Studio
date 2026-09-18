import cv2
import numpy as np

from app.services.live_preview import LivePreview, PreviewBox, render_preview_jpeg


def _decode(jpeg: bytes) -> np.ndarray:
    return cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)


def test_render_draws_the_box_outline_but_not_its_interior():
    frame = np.zeros((240, 320, 3), dtype=np.uint8)
    jpeg, width, height = render_preview_jpeg(frame, [PreviewBox((40, 60, 200, 180), "#1 car 90%", track_id=1)])

    image = _decode(jpeg)
    assert (width, height) == (320, 240)
    assert image.shape == (240, 320, 3)
    assert image[120, 40].max() > 100, "left edge of the box was not drawn"
    assert image[120, 120].max() < 30, "box interior should stay untouched"


def test_render_draws_on_a_copy_not_the_callers_frame():
    frame = np.zeros((48, 64, 3), dtype=np.uint8)
    render_preview_jpeg(frame, [PreviewBox((5, 5, 40, 40), "#0 car", track_id=0)])
    assert frame.max() == 0


def test_render_downscales_wide_frames_and_scales_boxes_with_them():
    frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
    jpeg, width, height = render_preview_jpeg(frame, [PreviewBox((960, 540, 1440, 900), "#2 bus", track_id=2)])

    assert (width, height) == (960, 540)
    image = _decode(jpeg)
    # The box's left edge is at x=960 in the source, so x=480 after halving.
    assert image[360, 480].max() > 100
    assert image[360, 600].max() < 30


def test_updates_inside_the_minimum_interval_are_skipped():
    now = [0.0]
    preview = LivePreview(min_interval_seconds=0.5, clock=lambda: now[0])
    frame = np.zeros((48, 64, 3), dtype=np.uint8)

    assert preview.update(frame, []) is True
    now[0] = 0.2
    assert preview.update(frame, []) is False
    assert preview.latest().sequence == 1

    now[0] = 0.6
    assert preview.update(frame, []) is True
    assert preview.latest().sequence == 2


def test_latest_is_none_before_any_frame():
    assert LivePreview().latest() is None
