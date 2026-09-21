"""One vehicle going past is one track.

A number plate at 7fps has moved clean off its own last position by
the next frame, so ByteTrack's overlap matching never follows it and
every frame of the same car arrives as its own single sighting: one
car past the camera, seventeen entries to review, sixteen of them the
same plate.

Linking them is motion, not overlap. Where a box was and how fast it
was going says where it will be next; two cars in the same lane stay
two chains because each one's own prediction fits it better.
"""

import pytest

from app.services.sighting_linker import link_single_sightings
from app.services.track_processor import _Observation


def _sighting(frame_index: int, bbox, class_id: int = 0, confirmed: bool = False) -> _Observation:
    return _Observation(
        frame_index=frame_index,
        timestamp_ms=frame_index * 100,
        bbox=bbox,
        class_id=class_id,
        confidence=0.9,
        confirmed=confirmed,
        crop=None,
        sharpness_score=100.0,
        blur_score=0.5,
        area_ratio=0.02,
        truncated=False,
        frame_width=1920,
        frame_height=1080,
    )


def _plate(frame_index: int, x: float, y: float = 500.0, size: float = 100.0):
    """A plate-sized box with its top-left at (x, y)."""
    return (x, y, x + size, y + size / 2)


def _chains(linked) -> list[list[int]]:
    """The frame indexes of each track, longest first."""
    return sorted(
        ([o.frame_index for o in observations] for observations in linked.values()),
        key=len,
        reverse=True,
    )


# --- one vehicle --------------------------------------------------------------


def test_a_plate_moving_steadily_becomes_one_track():
    """The case the whole thing exists for: seventeen entries for one
    car going past."""
    sightings = {1_000_000 + i: [_sighting(i, _plate(i, 100.0 + i * 120.0))] for i in range(6)}

    linked = link_single_sightings(sightings)

    assert _chains(linked) == [[0, 1, 2, 3, 4, 5]]


def test_the_observations_keep_their_own_frames():
    sightings = {1_000_000 + i: [_sighting(i, _plate(i, 100.0 + i * 120.0))] for i in range(3)}

    linked = link_single_sightings(sightings)

    observations = next(iter(linked.values()))
    assert [o.frame_index for o in observations] == [0, 1, 2]
    assert [o.bbox[0] for o in observations] == [100.0, 220.0, 340.0]


def test_it_links_across_a_frame_the_detector_missed():
    """A detector that misses one frame of six should not turn one
    vehicle into two."""
    steps = [0, 1, 3, 4]
    sightings = {1_000_000 + i: [_sighting(step, _plate(step, 100.0 + step * 120.0))] for i, step in enumerate(steps)}

    linked = link_single_sightings(sightings)

    assert _chains(linked) == [steps]


def test_a_long_gap_is_a_different_vehicle():
    """Half a second later in the same lane is the next car, not the
    same one."""
    near = {1_000_000: [_sighting(0, _plate(0, 100.0))], 1_000_001: [_sighting(1, _plate(1, 220.0))]}
    far = {1_000_002: [_sighting(20, _plate(20, 100.0))]}

    linked = link_single_sightings({**near, **far})

    assert _chains(linked) == [[0, 1], [20]]


# --- two vehicles at once -----------------------------------------------------


def test_two_vehicles_in_the_same_lane_get_their_own_ids():
    """"If two vehicles are coming in the lane at once, give them each
    their own tracking id" - both move, so both are predicted, and
    each prediction fits its own car."""
    sightings = {}
    for i in range(5):
        sightings[2_000_000 + i] = [_sighting(i, _plate(i, 100.0 + i * 120.0, y=400.0))]
        sightings[3_000_000 + i] = [_sighting(i, _plate(i, 900.0 + i * 120.0, y=700.0))]

    linked = link_single_sightings(sightings)

    assert _chains(linked) == [[0, 1, 2, 3, 4], [0, 1, 2, 3, 4]]


def test_two_sightings_on_one_frame_never_join_the_same_track():
    """A track is one thing in one place; it cannot be in two."""
    sightings = {
        1_000_000: [_sighting(0, _plate(0, 100.0, y=400.0))],
        1_000_001: [_sighting(1, _plate(1, 220.0, y=400.0))],
        1_000_002: [_sighting(1, _plate(1, 260.0, y=410.0))],
    }

    linked = link_single_sightings(sightings)

    for observations in linked.values():
        indexes = [o.frame_index for o in observations]
        assert len(indexes) == len(set(indexes))


def test_a_car_far_across_the_frame_is_not_the_same_car():
    sightings = {
        1_000_000: [_sighting(0, _plate(0, 100.0, y=200.0))],
        1_000_001: [_sighting(1, _plate(1, 1700.0, y=900.0))],
    }

    linked = link_single_sightings(sightings)

    assert _chains(linked) == [[0], [1]]


def test_a_box_of_a_very_different_size_is_not_the_same_thing():
    """A plate does not quadruple in size between two frames."""
    sightings = {
        1_000_000: [_sighting(0, _plate(0, 100.0, size=100.0))],
        1_000_001: [_sighting(1, _plate(1, 140.0, size=400.0))],
    }

    linked = link_single_sightings(sightings)

    assert _chains(linked) == [[0], [1]]


def test_different_classes_are_never_the_same_vehicle():
    sightings = {
        1_000_000: [_sighting(0, _plate(0, 100.0), class_id=0)],
        1_000_001: [_sighting(1, _plate(1, 220.0), class_id=3)],
    }

    linked = link_single_sightings(sightings)

    assert _chains(linked) == [[0], [1]]


# --- what it leaves alone -----------------------------------------------------


def test_a_followed_track_is_left_exactly_as_it_is():
    """ByteTrack managed to follow this one. Its judgement stands."""
    followed = {7: [_sighting(0, _plate(0, 100.0), confirmed=True), _sighting(1, _plate(1, 120.0), confirmed=True)]}

    linked = link_single_sightings(dict(followed))

    assert linked[7] == followed[7]


def test_a_sighting_is_not_linked_into_a_followed_track():
    """Dropping the ones a track absorbed is a separate judgement,
    made before this - see ``drop_sightings_absorbed_by_tracks``."""
    sightings = {
        7: [_sighting(0, _plate(0, 100.0), confirmed=True), _sighting(1, _plate(1, 220.0), confirmed=True)],
        1_000_000: [_sighting(2, _plate(2, 340.0))],
    }

    linked = link_single_sightings(sightings)

    assert len(linked[7]) == 2
    assert _chains(linked) == [[0, 1], [2]]


def test_nothing_at_all_is_handled():
    assert link_single_sightings({}) == {}


@pytest.mark.parametrize("count", [1, 2])
def test_the_ids_that_come_back_are_still_unique(count):
    sightings = {1_000_000 + i: [_sighting(i, _plate(i, 100.0 + i * 120.0))] for i in range(count)}

    linked = link_single_sightings(sightings)

    assert len(set(linked)) == len(linked)


# --- through the real pipeline ------------------------------------------------


def test_one_car_past_the_camera_is_one_track_to_review(tmp_path):
    """End to end, because this is what the person sees: seventeen
    rows in the Tracks list where there was one car."""
    from fastapi.testclient import TestClient

    from app.main import app
    from app.ml.types import Detection
    from tests.job_execution import process_source_sync
    from tests.video_factory import create_synthetic_video

    class _APlateGoingPast:
        """Moves too far each frame for overlap matching to follow -
        which is every real number plate at these frame rates."""

        model_version = "plate-going-past-v1"
        class_names = {0: "plate"}

        def __init__(self):
            self._seen = 0

        def detect(self, frame):
            x = 10.0 + self._seen * 40.0
            self._seen += 1
            return [Detection(bbox_xyxy=(x, 40.0, x + 24.0, 52.0), class_id=0, confidence=0.9)]

    client = TestClient(app)
    project = client.post("/projects", json={"name": "One Car One Track"}).json()
    video = create_synthetic_video(tmp_path / "clip.mp4", frame_count=8, fps=10.0, width=320, height=96)
    source = client.post(f"/projects/{project['id']}/sources", json={"path": str(video)}).json()
    process_source_sync(client, project["id"], source["id"], _APlateGoingPast(), target_fps=10.0)

    tracks = client.get(f"/projects/{project['id']}/tracks").json()

    assert len(tracks) == 1
