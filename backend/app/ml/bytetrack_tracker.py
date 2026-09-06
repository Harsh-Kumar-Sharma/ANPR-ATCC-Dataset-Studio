import numpy as np
import supervision as sv
from trackers import ByteTrackTracker as _ByteTrackTracker

from app.ml.types import Detection, TrackedDetection

#: Frames a track may go undetected before it's considered lost. Kept
#: generous so a blurred/fast vehicle isn't split into a second track
#: on a short detection gap, per docs/07_ML_CV_PIPELINE.md.
DEFAULT_LOST_TRACK_BUFFER = 30


class ByteTrackTracker:
    """Tracker adapter over the ``trackers`` package's ByteTrack implementation."""

    def __init__(self, frame_rate: float = 30.0, lost_track_buffer: int = DEFAULT_LOST_TRACK_BUFFER) -> None:
        self._tracker = _ByteTrackTracker(frame_rate=frame_rate, lost_track_buffer=lost_track_buffer)

    def update(self, detections: list[Detection], timestamp_ms: int) -> list[TrackedDetection]:
        sv_detections = _to_supervision_detections(detections)
        tracked = self._tracker.update(sv_detections, timestamp=timestamp_ms / 1000)

        results: list[TrackedDetection] = []
        for i in range(len(tracked)):
            track_id = int(tracked.tracker_id[i])
            if track_id < 0:
                continue  # not yet activated (needs consecutive-frame confirmation)
            results.append(
                TrackedDetection(
                    track_id=track_id,
                    bbox_xyxy=tuple(float(v) for v in tracked.xyxy[i]),
                    class_id=int(tracked.class_id[i]),
                    confidence=float(tracked.confidence[i]),
                )
            )
        return results


def _to_supervision_detections(detections: list[Detection]) -> sv.Detections:
    if not detections:
        return sv.Detections.empty()
    return sv.Detections(
        xyxy=np.array([d.bbox_xyxy for d in detections], dtype=np.float32),
        confidence=np.array([d.confidence for d in detections], dtype=np.float32),
        class_id=np.array([d.class_id for d in detections], dtype=int),
    )
