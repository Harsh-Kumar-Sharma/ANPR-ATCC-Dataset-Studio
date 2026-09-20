from app.ml.bytetrack_tracker import ByteTrackTracker
from app.ml.types import Detection


def test_track_activates_after_consecutive_frames_and_stays_stable():
    tracker = ByteTrackTracker(frame_rate=5.0)

    # First frame: not yet activated. It comes back as a single
    # sighting rather than being dropped - a detection the tracker
    # cannot follow yet is still a detection.
    result_0 = tracker.update([Detection(bbox_xyxy=(10, 10, 60, 40), class_id=0, confidence=0.9)], timestamp_ms=0)
    assert len(result_0) == 1
    assert result_0[0].confirmed is False

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


def test_a_detection_the_tracker_cannot_follow_is_still_kept():
    """The bug this closes: a real model detected a real number plate
    in nine frames out of ten and the app persisted nothing at all.

    ByteTrack activates a track only after matching the same box
    across consecutive frames. Something small and fast never
    matches - a plate at 7fps has moved clean off its own last
    position by the next frame - so it was reported unconfirmed every
    time and dropped every time.
    """
    tracker = ByteTrackTracker(frame_rate=7.0)
    kept = []

    for index, x in enumerate([200.0, 520.0, 840.0, 1160.0]):
        kept += tracker.update(
            [Detection(bbox_xyxy=(x, 500.0, x + 90.0, 530.0), class_id=0, confidence=0.55)],
            timestamp_ms=int(index / 7 * 1000),
        )

    assert len(kept) == 4, "every sighting is kept"
    assert all(not d.confirmed for d in kept), "and none of them pretends to be a followed track"
    assert len({d.track_id for d in kept}) == 4, "each stands on its own rather than sharing an id"


def test_single_sightings_never_collide_with_real_track_ids():
    tracker = ByteTrackTracker(frame_rate=25.0)
    sighting = tracker.update(
        [Detection(bbox_xyxy=(10, 10, 60, 40), class_id=0, confidence=0.9)], timestamp_ms=0
    )[0]
    followed = tracker.update(
        [Detection(bbox_xyxy=(12, 10, 62, 40), class_id=0, confidence=0.9)], timestamp_ms=40
    )[0]

    assert sighting.track_id != followed.track_id
    assert sighting.track_id > followed.track_id


def test_single_sightings_can_be_turned_off():
    """Counting vehicles wants followed tracks only; building a
    dataset wants every sighting."""
    tracker = ByteTrackTracker(frame_rate=7.0, keep_single_sightings=False)

    result = tracker.update(
        [Detection(bbox_xyxy=(200.0, 500.0, 290.0, 530.0), class_id=0, confidence=0.55)], timestamp_ms=0
    )

    assert result == []
