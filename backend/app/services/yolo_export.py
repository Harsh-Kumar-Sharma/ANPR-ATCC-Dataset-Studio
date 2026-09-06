Bbox = tuple[float, float, float, float]


def bbox_relative_to_crop(annotation_bbox: Bbox, original_frame_bbox: Bbox) -> Bbox:
    """Map a (possibly human-edited) full-frame bbox into the pixel
    space of the saved crop image.

    The saved frame_candidates image is a crop taken at the
    *detector's* original bbox (see docs/HANDOFF.md Phase 2/4 notes) -
    it is never regenerated when a human edits the bbox during review.
    So the crop's own pixel width/height equal
    ``original_frame_bbox``'s width/height, and any edited bbox must
    be translated into that same local origin before it can be
    normalized against the actual exported image. This is the same
    math the desktop review UI uses for its live bbox overlay -
    keeping both consistent was deliberate.
    """
    ox1, oy1, ox2, oy2 = original_frame_bbox
    ax1, ay1, ax2, ay2 = annotation_bbox
    return (ax1 - ox1, ay1 - oy1, ax2 - ox1, ay2 - oy1)


def normalize_yolo_bbox(local_bbox: Bbox, image_width: int, image_height: int) -> tuple[float, float, float, float]:
    """Convert a crop-local xyxy bbox into YOLO's normalized
    (x_center, y_center, width, height), clamped to the image bounds
    so an edited bbox that overshoots the crop still yields a valid
    label instead of an out-of-range one."""
    x1, y1, x2, y2 = local_bbox
    x1 = max(0.0, min(x1, image_width))
    x2 = max(0.0, min(x2, image_width))
    y1 = max(0.0, min(y1, image_height))
    y2 = max(0.0, min(y2, image_height))

    width = max(0.0, x2 - x1)
    height = max(0.0, y2 - y1)
    x_center = x1 + width / 2
    y_center = y1 + height / 2

    return (
        x_center / image_width,
        y_center / image_height,
        width / image_width,
        height / image_height,
    )


def format_yolo_label_line(class_index: int, normalized_bbox: tuple[float, float, float, float]) -> str:
    x, y, w, h = normalized_bbox
    return f"{class_index} {x:.6f} {y:.6f} {w:.6f} {h:.6f}"
