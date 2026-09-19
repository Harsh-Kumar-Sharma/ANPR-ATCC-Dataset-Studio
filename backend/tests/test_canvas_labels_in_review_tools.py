"""Ticket 14: the screens that review labelling can see canvas labels.

A project labelled entirely in the canvas used to get an empty
disagreement queue and an empty class distribution, both of which read
as "nothing wrong" rather than "not looked at". These are the two
questions a labeller asks about their own work, and neither could be
answered about work done the new way.
"""

from fastapi.testclient import TestClient

from app.main import app
from app.ml.types import Detection
from tests.job_execution import process_source_sync, tracks_for_run
from tests.video_factory import create_synthetic_video

client = TestClient(app)

WIDTH, HEIGHT = 128, 96

#: Where the detector puts its vehicle. Tests draw human boxes relative
#: to this so the overlap is deliberate rather than accidental.
DETECTED = (20.0, 20.0, 80.0, 70.0)
#: Same object, drawn a little tighter by a human. Well above the match
#: threshold.
SAME_OBJECT = [24.0, 24.0, 78.0, 68.0]
#: Nowhere near it - a vehicle the detector missed entirely.
ELSEWHERE = [90.0, 5.0, 125.0, 40.0]

CAR_CLASS = 4  # "Car/Jeep/Van"
BUS_CLASS = 8  # implausible for a detector that said "car"


class _OneCarDetector:
    """One car, in the same place, in every frame."""

    model_version = "one-car-stub-v1"
    class_names = {0: "car"}

    def detect(self, frame):
        return [Detection(bbox_xyxy=DETECTED, class_id=0, confidence=0.9)]


def _project_with_queue(tmp_path, name: str, frame_count: int = 8):
    project = client.post("/projects", json={"name": name}).json()
    video = create_synthetic_video(tmp_path / "clip.mp4", frame_count=frame_count, fps=10.0, width=WIDTH, height=HEIGHT)
    source = client.post(f"/projects/{project['id']}/sources", json={"path": str(video)}).json()
    submitted = process_source_sync(client, project["id"], source["id"], _OneCarDetector(), target_fps=10.0)
    frames = client.get(f"/projects/{project['id']}/frames").json()
    assert frames
    return project, frames, submitted


def _draw(frame: dict, boxes: list[tuple[int | None, list[float]]]):
    response = client.put(
        f"/frames/{frame['id']}/annotations",
        json={"annotations": [{"class_id": class_id, "bbox_json": bbox} for class_id, bbox in boxes]},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _disagreements(project: dict) -> list[dict]:
    response = client.get(f"/projects/{project['id']}/active-learning/disagreements")
    assert response.status_code == 200, response.text
    return response.json()


# --- the disagreement queue -------------------------------------------------


def test_a_canvas_box_that_contradicts_the_detector_reaches_the_queue(tmp_path):
    """The whole ticket. The detector saw a car; the human drew a box over
    the same vehicle and called it a bus. That is worth a second look
    whichever way the label was written."""
    project, frames, _ = _project_with_queue(tmp_path, "Canvas Disagreement")
    _draw(frames[0], [(BUS_CLASS, SAME_OBJECT)])

    items = _disagreements(project)

    assert len(items) == 1, items
    item = items[0]
    assert item["kind"] == "class_mismatch"
    assert item["detector_class"] == "car"
    assert item["human_class_id"] == BUS_CLASS
    assert item["frame_id"] == frames[0]["id"]


def test_a_canvas_box_agreeing_with_the_detector_is_not_flagged(tmp_path):
    project, frames, _ = _project_with_queue(tmp_path, "Canvas Agreement")
    _draw(frames[0], [(CAR_CLASS, SAME_OBJECT)])

    assert _disagreements(project) == []


def test_a_box_where_the_detector_found_nothing_is_reported_as_a_miss(tmp_path):
    """Deliberate, and the most valuable signal here: a human drew a
    vehicle the detector did not find at all. Dropping it because there
    is no detection to compare against would throw away the one case
    that is unambiguously the model's fault."""
    project, frames, _ = _project_with_queue(tmp_path, "Detector Miss")
    _draw(frames[0], [(CAR_CLASS, SAME_OBJECT), (CAR_CLASS, ELSEWHERE)])

    items = _disagreements(project)

    assert len(items) == 1, items
    item = items[0]
    assert item["kind"] == "missed_detection"
    assert item["detector_class"] is None
    assert item["track_id"] is None
    assert item["frame_id"] == frames[0]["id"]
    assert item["human_class_id"] == CAR_CLASS


def test_a_canvas_box_has_no_track_even_when_it_matches_a_detection(tmp_path):
    """Matching a detection by overlap does not make the label *about*
    that detection's track - opening the track would not show the box.
    Null is what lets the panel offer the frame instead of a Review
    button that goes somewhere unhelpful."""
    project, frames, _ = _project_with_queue(tmp_path, "Canvas No Track")
    _draw(frames[0], [(BUS_CLASS, SAME_OBJECT)])

    item = _disagreements(project)[0]

    assert item["kind"] == "class_mismatch", "it did match a detection"
    assert item["detector_class"] == "car"
    assert item["track_id"] is None
    assert item["frame_id"] == frames[0]["id"]


def test_track_review_labels_still_reach_the_queue_with_their_track(tmp_path):
    """The old path is unchanged, including the track id a reviewer
    navigates by. Its annotation links straight to the detection, so
    nothing is matched by overlap."""
    project, frames, submitted = _project_with_queue(tmp_path, "Track Disagreement")
    track = tracks_for_run(client, project["id"], submitted["run_id"])[0]
    timeline = client.get(f"/tracks/{track['id']}").json()
    reviewed = client.put(
        f"/tracks/{track['id']}/review",
        json={"frame_candidate_id": timeline["frames"][0]["id"], "decision": "accepted", "class_id": BUS_CLASS},
    )
    assert reviewed.status_code == 200, reviewed.text

    items = _disagreements(project)

    assert len(items) == 1, items
    assert items[0]["kind"] == "class_mismatch"
    assert items[0]["track_id"] == track["id"]
    assert items[0]["detector_class"] == "car"


def test_a_label_on_a_rejected_frame_is_not_worth_reviewing(tmp_path):
    """A rejected frame is out of the dataset, so its labels are not
    going to train anything. Queueing them would be asking for work that
    changes nothing."""
    project, frames, _ = _project_with_queue(tmp_path, "Rejected Not Queued")
    _draw(frames[0], [(BUS_CLASS, SAME_OBJECT)])
    assert len(_disagreements(project)) == 1

    client.put(f"/frames/{frames[0]['id']}/status", json={"status": "rejected"})

    assert _disagreements(project) == []


def test_an_unclassified_box_is_not_a_disagreement(tmp_path):
    """Nothing to disagree with yet."""
    project, frames, _ = _project_with_queue(tmp_path, "Unclassified Box")
    _draw(frames[0], [(None, ELSEWHERE)])

    assert _disagreements(project) == []


# --- the class balance of a labeller's own work ------------------------------


def _balance(project: dict) -> dict:
    response = client.get(f"/projects/{project['id']}/label-balance")
    assert response.status_code == 200, response.text
    return response.json()


def test_the_class_balance_counts_canvas_boxes(tmp_path):
    """The question a labeller actually asks: what have I got? The
    evaluation report answers it per processing run, which a canvas box
    does not belong to, so a canvas labeller could not ask it at all."""
    project, frames, _ = _project_with_queue(tmp_path, "Balance Canvas")
    _draw(frames[0], [(CAR_CLASS, SAME_OBJECT), (CAR_CLASS, ELSEWHERE)])
    _draw(frames[1], [(BUS_CLASS, SAME_OBJECT)])

    balance = _balance(project)

    by_class = {row["class_id"]: row for row in balance["classes"]}
    assert by_class[CAR_CLASS]["box_count"] == 2
    assert by_class[BUS_CLASS]["box_count"] == 1
    assert by_class[CAR_CLASS]["name"] == "Car/Jeep/Van"
    assert balance["total_boxes"] == 3
    assert balance["labeled_frames"] == 2


def test_the_class_balance_counts_track_review_labels_too(tmp_path):
    """"Whatever path the labels were written by" is the criterion."""
    project, frames, submitted = _project_with_queue(tmp_path, "Balance Both")
    track = tracks_for_run(client, project["id"], submitted["run_id"])[0]
    timeline = client.get(f"/tracks/{track['id']}").json()
    reviewed = client.put(
        f"/tracks/{track['id']}/review",
        json={"frame_candidate_id": timeline["frames"][0]["id"], "decision": "accepted", "class_id": CAR_CLASS},
    )
    assert reviewed.status_code == 200, reviewed.text
    # A different frame: saving boxes on the reviewed one would replace
    # its whole set and delete the label under test.
    reviewed_frame_id = reviewed.json()["annotation"]["frame_id"]
    other = next(f for f in frames if f["id"] != reviewed_frame_id)
    _draw(other, [(BUS_CLASS, SAME_OBJECT)])

    balance = _balance(project)

    by_class = {row["class_id"]: row["box_count"] for row in balance["classes"]}
    assert by_class.get(CAR_CLASS) == 1
    assert by_class.get(BUS_CLASS) == 1
    assert balance["total_boxes"] == 2


def test_the_class_balance_counts_frames_labelled_as_empty(tmp_path):
    """Background frames are part of the balance of a dataset - arguably
    the part most easily got wrong - so they are counted rather than
    invisible."""
    project, frames, _ = _project_with_queue(tmp_path, "Balance Background")
    _draw(frames[0], [(CAR_CLASS, SAME_OBJECT)])
    _draw(frames[1], [])

    balance = _balance(project)

    assert balance["labeled_frames"] == 2
    assert balance["background_frames"] == 1
    assert balance["total_boxes"] == 1


def test_the_class_balance_counts_boxes_still_missing_a_class(tmp_path):
    """Unfinished work does not export. Knowing how much of it there is
    is the difference between "my dataset is small" and "my dataset is
    small because I have forty boxes to classify"."""
    project, frames, _ = _project_with_queue(tmp_path, "Balance Unclassified")
    _draw(frames[0], [(CAR_CLASS, SAME_OBJECT), (None, ELSEWHERE)])

    balance = _balance(project)

    assert balance["total_boxes"] == 1
    assert balance["unclassified_boxes"] == 1


def test_the_class_balance_leaves_out_rejected_frames(tmp_path):
    project, frames, _ = _project_with_queue(tmp_path, "Balance Rejected")
    _draw(frames[0], [(CAR_CLASS, SAME_OBJECT)])
    _draw(frames[1], [(BUS_CLASS, SAME_OBJECT)])
    client.put(f"/frames/{frames[1]['id']}/status", json={"status": "rejected"})

    balance = _balance(project)

    assert balance["total_boxes"] == 1
    assert balance["labeled_frames"] == 1


def test_an_unlabelled_project_reports_an_empty_balance_rather_than_failing(tmp_path):
    project, _frames, _ = _project_with_queue(tmp_path, "Balance Empty")

    balance = _balance(project)

    assert balance["classes"] == []
    assert balance["total_boxes"] == 0
    assert balance["labeled_frames"] == 0
