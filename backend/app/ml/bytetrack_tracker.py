import numpy as np
import supervision as sv
from trackers import ByteTrackTracker as _ByteTrackTracker

from app.ml.types import Detection, TrackedDetection

#: Frames a track may go undetected before it's considered lost. Kept
#: generous so a blurred/fast vehicle isn't split into a second track
#: on a short detection gap, per docs/07_ML_CV_PIPELINE.md.
DEFAULT_LOST_TRACK_BUFFER = 30

#: Where ids for single sightings start. Far above anything ByteTrack
#: will count to in a session, so the two cannot collide.
SINGLE_SIGHTING_ID_BASE = 1_000_000


class ByteTrackTracker:
    """Tracker adapter over the ``trackers`` package's ByteTrack implementation."""

    def __init__(
        self,
        frame_rate: float = 30.0,
        lost_track_buffer: int = DEFAULT_LOST_TRACK_BUFFER,
        keep_single_sightings: bool = True,
    ) -> None:
        self._tracker = _ByteTrackTracker(frame_rate=frame_rate, lost_track_buffer=lost_track_buffer)
        self._keep_single_sightings = keep_single_sightings
        self._next_sighting = SINGLE_SIGHTING_ID_BASE

    def update(self, detections: list[Detection], timestamp_ms: int) -> list[TrackedDetection]:
        sv_detections = _to_supervision_detections(detections)
        tracked = self._tracker.update(sv_detections, timestamp=timestamp_ms / 1000)

        results: list[TrackedDetection] = []
        for i in range(len(tracked)):
            track_id = int(tracked.tracker_id[i])
            confirmed = track_id >= 0

            if not confirmed:
                # ByteTrack activates a track only once it has matched
                # the same box across consecutive frames. That never
                # happens for something small and fast: a number plate
                # at 7fps has moved clean off its own last position by
                # the next frame, so it is reported unconfirmed every
                # time and used to be dropped every time. The result
                # was a real model detecting a real vehicle in nine
                # frames out of ten and the app persisting nothing at
                # all.
                #
                # A detection the tracker cannot follow is still a
                # detection. It is kept as its own single sighting -
                # honest about being one frame long rather than
                # invented into a track that was never observed.
                if not self._keep_single_sightings:
                    continue
                track_id = self._next_sighting
                self._next_sighting += 1

            results.append(
                TrackedDetection(
                    track_id=track_id,
                    bbox_xyxy=tuple(float(v) for v in tracked.xyxy[i]),
                    class_id=int(tracked.class_id[i]),
                    confidence=float(tracked.confidence[i]),
                    confirmed=confirmed,
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
