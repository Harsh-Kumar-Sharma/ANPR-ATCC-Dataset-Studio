from dataclasses import dataclass

Bbox = tuple[float, float, float, float]


@dataclass(frozen=True)
class TrackSummary:
    """The minimal shape needed to evaluate one track for duplicate/
    fragmentation candidacy against every other track in the same run."""

    track_id: str
    start_ts: int
    end_ts: int
    first_bbox: Bbox
    last_bbox: Bbox


def iou(a: Bbox, b: Bbox) -> float:
    """Intersection over union of two ``[x1, y1, x2, y2]`` boxes.

    Public because more than one question in this codebase is "are these
    two boxes the same object" - duplicate tracks here, and matching a
    human's box to a detection in ``active_learning``. One definition so
    the two cannot answer it differently.
    """
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    intersection = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    if intersection <= 0:
        return 0.0
    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    union = area_a + area_b - intersection
    return intersection / union if union > 0 else 0.0


#: Uncalibrated defaults - same caveat as every other threshold in
#: this codebase (detector confidence, ranking, OCR confidence): a
#: reasonable starting point, not tuned against real footage.
DEFAULT_DUPLICATE_IOU_THRESHOLD = 0.5
DEFAULT_FRAGMENTATION_GAP_MS = 500
DEFAULT_FRAGMENTATION_IOU_THRESHOLD = 0.3


def find_duplicate_track_pairs(
    tracks: list[TrackSummary], iou_threshold: float = DEFAULT_DUPLICATE_IOU_THRESHOLD
) -> list[tuple[str, str]]:
    """Two tracks that overlap in time AND in space are likely the
    same physical vehicle counted twice (a tracker association
    failure), not two different vehicles. Heuristic - see
    docs/07_ML_CV_PIPELINE.md "duplicate-track rate"."""
    pairs = []
    for i, a in enumerate(tracks):
        for b in tracks[i + 1 :]:
            time_overlaps = a.start_ts <= b.end_ts and b.start_ts <= a.end_ts
            if not time_overlaps:
                continue
            if iou(a.first_bbox, b.first_bbox) >= iou_threshold or iou(a.last_bbox, b.last_bbox) >= iou_threshold:
                pairs.append((a.track_id, b.track_id))
    return pairs


def find_fragmented_track_pairs(
    tracks: list[TrackSummary],
    gap_threshold_ms: int = DEFAULT_FRAGMENTATION_GAP_MS,
    iou_threshold: float = DEFAULT_FRAGMENTATION_IOU_THRESHOLD,
) -> list[tuple[str, str]]:
    """One track ending just before another starts, in nearly the same
    place, likely means one real vehicle was split into two tracks
    (e.g. a short detection gap the tracker couldn't bridge) - see
    docs/07_ML_CV_PIPELINE.md "track fragmentation"."""
    pairs = []
    ordered = sorted(tracks, key=lambda t: t.start_ts)
    for i, earlier in enumerate(ordered):
        for later in ordered[i + 1 :]:
            gap = later.start_ts - earlier.end_ts
            if gap < 0:
                continue  # they overlap in time - that's duplicate territory, not fragmentation
            if gap > gap_threshold_ms:
                break  # ordered by start_ts, so no later track can be closer
            if iou(earlier.last_bbox, later.first_bbox) >= iou_threshold:
                pairs.append((earlier.track_id, later.track_id))
    return pairs
