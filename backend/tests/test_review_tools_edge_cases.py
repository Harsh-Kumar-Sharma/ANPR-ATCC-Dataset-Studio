"""The cases ticket 14 got wrong, found in review.

Each of these was a confident wrong answer rather than a missing one:
a balance that reported zero labelled frames for a project full of
labels, a disagreement raised against a detection that was not the one
the human labelled, and a "the detector found nothing" claim made about
a vehicle it had in fact found.
"""

from app.ml.types import Detection
from tests.job_execution import process_source_sync, tracks_for_run
from tests.test_canvas_labels_in_review_tools import (  # noqa: F401
    BUS_CLASS,
    CAR_CLASS,
    DETECTED,
    ELSEWHERE,
    HEIGHT,
    SAME_OBJECT,
    WIDTH,
    _balance,
    _disagreements,
    _draw,
    _OneCarDetector,
    _project_with_queue,
    client,
)
from tests.video_factory import create_synthetic_video


def _review(track: dict, class_id: int, decision: str = "accepted") -> dict:
    timeline = client.get(f"/tracks/{track['id']}").json()
    response = client.put(
        f"/tracks/{track['id']}/review",
        json={"frame_candidate_id": timeline["frames"][0]["id"], "decision": decision, "class_id": class_id},
    )
    assert response.status_code == 200, response.text
    return response.json()["annotation"]


# --- the balance has to describe both ways of labelling ----------------------


def test_a_frame_labelled_through_track_review_counts_as_labelled(tmp_path):
    """`Frame.status` is only ever set by a canvas save, so counting it
    alone reported "nineteen boxes across zero labelled frames" for a
    project that had been reviewed track by track - which is what the
    user's own data looks like."""
    project, frames, submitted = _project_with_queue(tmp_path, "Balance Track Frames")
    track = tracks_for_run(client, project["id"], submitted["run_id"])[0]
    _review(track, CAR_CLASS)

    balance = _balance(project)

    assert balance["total_boxes"] == 1
    assert balance["labeled_frames"] == 1, "a reviewed frame is a frame somebody has labelled"


def test_both_ways_of_labelling_count_towards_the_same_frame_total(tmp_path):
    project, frames, submitted = _project_with_queue(tmp_path, "Balance Both Frames")
    track = tracks_for_run(client, project["id"], submitted["run_id"])[0]
    reviewed = _review(track, CAR_CLASS)
    other = next(f for f in frames if f["id"] != reviewed["frame_id"])
    _draw(other, [(BUS_CLASS, SAME_OBJECT)])

    balance = _balance(project)

    assert balance["labeled_frames"] == 2
    assert balance["total_boxes"] == 2


def test_one_frame_labelled_twice_over_is_still_one_frame(tmp_path):
    """A frame reviewed through a track and then opened on the canvas
    must not be counted once for each."""
    project, frames, submitted = _project_with_queue(tmp_path, "Balance No Double Count")
    track = tracks_for_run(client, project["id"], submitted["run_id"])[0]
    reviewed = _review(track, CAR_CLASS)
    frame = next(f for f in frames if f["id"] == reviewed["frame_id"])
    _draw(frame, [(CAR_CLASS, SAME_OBJECT), (BUS_CLASS, ELSEWHERE)])

    balance = _balance(project)

    assert balance["labeled_frames"] == 1
    assert balance["total_boxes"] == 2


def test_a_rejected_frame_is_not_a_frame_somebody_labelled(tmp_path):
    project, frames, _ = _project_with_queue(tmp_path, "Balance Rejected Frames")
    _draw(frames[0], [(CAR_CLASS, SAME_OBJECT)])
    _draw(frames[1], [(CAR_CLASS, SAME_OBJECT)])
    client.put(f"/frames/{frames[1]['id']}/status", json={"status": "rejected"})

    balance = _balance(project)

    assert balance["labeled_frames"] == 1


def test_a_frame_holding_only_a_hard_label_still_counts_as_worked_on(tmp_path):
    """No boxes a dataset would take, but somebody did look at it. The
    two numbers say different things and both are worth having."""
    project, frames, submitted = _project_with_queue(tmp_path, "Balance Hard Frame")
    track = tracks_for_run(client, project["id"], submitted["run_id"])[0]
    _review(track, CAR_CLASS, decision="hard")

    balance = _balance(project)

    assert balance["total_boxes"] == 0
    assert balance["labeled_frames"] == 1
    assert balance["background_frames"] == 0, "a frame holding a hard box is not an empty picture"


# --- matching a box to the right detection -----------------------------------


class _CarAndBusDetector:
    """Two vehicles almost on top of each other - the ordinary dense
    case on gantry footage, and the one best-overlap got wrong."""

    model_version = "car-and-bus-stub-v1"
    class_names = {0: "car", 1: "bus"}

    def detect(self, frame):
        return [
            Detection(bbox_xyxy=(20.0, 20.0, 80.0, 70.0), class_id=0, confidence=0.9),
            Detection(bbox_xyxy=(22.0, 22.0, 82.0, 72.0), class_id=1, confidence=0.9),
        ]


def _project_with_two_detections(tmp_path, name: str):
    project = client.post("/projects", json={"name": name}).json()
    video = create_synthetic_video(tmp_path / "clip.mp4", frame_count=8, fps=10.0, width=WIDTH, height=HEIGHT)
    source = client.post(f"/projects/{project['id']}/sources", json={"path": str(video)}).json()
    process_source_sync(client, project["id"], source["id"], _CarAndBusDetector(), target_fps=10.0)
    frames = client.get(f"/projects/{project['id']}/frames").json()
    assert frames
    return project, frames


def test_a_class_any_overlapping_detection_allows_for_is_not_a_disagreement(tmp_path):
    """Two detections overlap the human's box equally. Asking only the
    highest-scoring one - and breaking the tie on database row order -
    flagged a correct label as wrong, at random."""
    project, frames = _project_with_two_detections(tmp_path, "Two Detections")
    _draw(frames[0], [(BUS_CLASS, SAME_OBJECT)])

    assert _disagreements(project) == []


def test_a_class_no_overlapping_detection_allows_for_is_still_flagged(tmp_path):
    """The other half of the same rule - it must not have become a way of
    never disagreeing with anything."""
    project, frames = _project_with_two_detections(tmp_path, "Two Detections Wrong")
    _draw(frames[0], [(1, SAME_OBJECT)])  # a two-wheeler, which neither a car nor a bus allows for

    items = _disagreements(project)

    assert len(items) == 1, items
    assert items[0]["kind"] == "class_mismatch"
    assert items[0]["detector_class"] in {"car", "bus"}


def test_a_box_matching_no_detection_says_so_without_claiming_more(tmp_path):
    """The entry means "nothing the detector found matches this box",
    which is what the data supports. Claiming the detector found nothing
    *here* was false whenever a real match fell below the threshold."""
    project, frames, _ = _project_with_queue(tmp_path, "Unmatched Box")
    _draw(frames[0], [(CAR_CLASS, ELSEWHERE)])

    items = _disagreements(project)

    assert len(items) == 1
    assert items[0]["kind"] == "unmatched_box"
    assert items[0]["detector_class"] is None


def test_a_human_who_tightens_a_loose_detection_is_not_told_it_was_missed(tmp_path):
    """A concentric box at 70% of each side overlaps 49%. Redrawing a
    loose truck box that tightly is ordinary, and the old threshold
    turned it into a false accusation against the model."""
    project, frames, _ = _project_with_queue(tmp_path, "Tightened Box")
    x1, y1, x2, y2 = DETECTED
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    width, height = (x2 - x1) * 0.7, (y2 - y1) * 0.7
    tightened = [cx - width / 2, cy - height / 2, cx + width / 2, cy + height / 2]

    _draw(frames[0], [(CAR_CLASS, tightened)])

    assert _disagreements(project) == [], "the detector found this vehicle; the human just drew it better"


# --- a queue a person can actually read --------------------------------------


def test_the_disagreement_queue_is_limited_like_its_siblings(tmp_path):
    """Every canvas box the detector missed is an entry. On real footage
    that is thousands, all serialised into one list with no paging - the
    two queues either side of it have taken a limit since Phase 8."""
    project, frames, _ = _project_with_queue(tmp_path, "Limited Queue", frame_count=8)
    for frame in frames[:5]:
        _draw(frame, [(CAR_CLASS, ELSEWHERE)])

    unlimited = _disagreements(project)
    assert len(unlimited) == 5

    limited = client.get(f"/projects/{project['id']}/active-learning/disagreements", params={"limit": 2}).json()
    assert len(limited) == 2
