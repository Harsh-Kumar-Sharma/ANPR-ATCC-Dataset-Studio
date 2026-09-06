from app.ml.bytetrack_tracker import ByteTrackTracker
from app.ml.types import Detection


def test_track_activates_after_consecutive_frames_and_stays_stable():
    tracker = ByteTrackTracker(frame_rate=5.0)

    # First frame: not yet activated, so no tracked detections come back.
    result_0 = tracker.update([Detection(bbox_xyxy=(10, 10, 60, 40), class_id=0, confidence=0.9)], timestamp_ms=0)
    assert result_0 == []

    # Second consecutive consistent detection activates the track.
    result_1 = tracker.update([Detection(bbox_xyxy=(12, 10, 62, 40), class_id=0, confidence=0.9)], timestamp_ms=200)
    assert len(result_1) == 1
    track_id = result_1[0].track_id
    assert track_id >= 0

    # Same track persists across subsequent frames.
    result_2 = tracker.update([Detection(bbox_xyxy=(14, 10, 64, 40), class_id=0, confidence=0.9)], timestamp_ms=400)
    assert len(result_2) == 1
    assert result_2[0].track_id == track_id


def test_empty_detections_produce_no_tracks():
    tracker = ByteTrackTracker(frame_rate=5.0)
    assert tracker.update([], timestamp_ms=0) == []


def test_two_distant_detections_get_different_track_ids():
    tracker = ByteTrackTracker(frame_rate=5.0)
    for timestamp_ms in (0, 200):
        result = tracker.update(
            [
                Detection(bbox_xyxy=(0, 0, 20, 20), class_id=0, confidence=0.9),
                Detection(bbox_xyxy=(500, 500, 520, 520), class_id=0, confidence=0.9),
            ],
            timestamp_ms=timestamp_ms,
        )
    track_ids = {r.track_id for r in result}
    assert len(track_ids) == 2
