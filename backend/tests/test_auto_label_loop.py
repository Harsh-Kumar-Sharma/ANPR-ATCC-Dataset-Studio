"""From a detection to a labelled frame in the dataset.

The loop the user actually works: the model finds a plate, they look
at what it found, accept it, and that becomes training data. Two
links in that chain were missing - accepting did not make the frame
labelled, so the export left it out, and the canvas opened empty on
a frame the model had already solved.
"""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.ml.types import Detection
from tests.job_execution import process_source_sync
from tests.video_factory import create_synthetic_video

client = TestClient(app)

WIDTH, HEIGHT = 128, 96
PLATE_CLASS = 4


class _OneCarDetector:
    model_version = "one-car-stub-v1"
    class_names = {0: "car"}

    def detect(self, frame):
        return [Detection(bbox_xyxy=(20.0, 20.0, 80.0, 70.0), class_id=0, confidence=0.9)]


def _reviewable(tmp_path, name: str) -> tuple[dict, dict, dict]:
    """A project with one detected track ready to review."""
    project = client.post("/projects", json={"name": name}).json()
    video = create_synthetic_video(tmp_path / "clip.mp4", frame_count=6, fps=10.0, width=WIDTH, height=HEIGHT)
    source = client.post(f"/projects/{project['id']}/sources", json={"path": str(video)}).json()
    process_source_sync(client, project["id"], source["id"], _OneCarDetector(), target_fps=10.0)

    tracks = client.get(f"/projects/{project['id']}/tracks").json()
    timeline = client.get(f"/tracks/{tracks[0]['id']}").json()
    return project, tracks[0], timeline["frames"][0]


def _accept(track: dict, candidate: dict, class_id: int | None = PLATE_CLASS) -> dict:
    response = client.put(
        f"/tracks/{track['id']}/review",
        json={
            "decision": "accepted",
            "frame_candidate_id": candidate["id"],
            "class_id": class_id,
            "bbox_json": candidate["bbox_json"],
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


# --- accepting is labelling ---------------------------------------------------


def test_accepting_a_track_makes_its_frame_labelled(tmp_path):
    """Until this, everything the user accepted stayed "pending" -
    the one state the dataset export refuses to take."""
    project, track, candidate = _reviewable(tmp_path, "Accept Labels It")

    _accept(track, candidate)

    frame = client.get(f"/frames/{candidate['frame_id']}").json()
    assert frame["status"] == "labeled"


def test_an_accepted_frame_reaches_the_dataset(tmp_path):
    """The whole point of accepting it."""
    project, track, candidate = _reviewable(tmp_path, "Accept Reaches Dataset")

    _accept(track, candidate)
    exported = client.post(f"/projects/{project['id']}/dataset-versions", json={})

    assert exported.status_code in (200, 201), exported.text
    assert sum(exported.json()["counts"].values()) >= 1


def test_accepting_without_a_class_is_refused_outright(tmp_path):
    """A box with no class exports as unclassified, so there would be
    nothing to label the frame with. The request schema already
    refuses it; this is here so that stays true."""
    project, track, candidate = _reviewable(tmp_path, "Accept Without A Class")

    refused = client.put(
        f"/tracks/{track['id']}/review",
        json={
            "decision": "accepted",
            "frame_candidate_id": candidate["id"],
            "class_id": None,
            "bbox_json": candidate["bbox_json"],
        },
    )

    assert refused.status_code == 422
    assert client.get(f"/frames/{candidate['frame_id']}").json()["status"] != "labeled"


def test_marking_a_track_hard_does_not_label_its_frame(tmp_path):
    """Hard is a judgement about the detection, not a label."""
    project, track, candidate = _reviewable(tmp_path, "Hard Is Not A Label")

    client.put(
        f"/tracks/{track['id']}/review",
        json={
            "decision": "hard",
            "frame_candidate_id": candidate["id"],
            "class_id": PLATE_CLASS,
            "bbox_json": candidate["bbox_json"],
        },
    )

    assert client.get(f"/frames/{candidate['frame_id']}").json()["status"] != "labeled"


# --- the canvas opens with what the model found -------------------------------


def test_a_frame_offers_the_boxes_the_model_found(tmp_path):
    """It used to open completely empty, and had to be drawn from
    scratch next to a review screen that already knew where the plate
    was."""
    project, track, candidate = _reviewable(tmp_path, "Suggestions Exist")

    suggestions = client.get(f"/frames/{candidate['frame_id']}/suggestions").json()

    assert len(suggestions) >= 1
    assert suggestions[0]["bbox_json"] == candidate["bbox_json"]
    assert suggestions[0]["detector_class"] == "car"


def test_the_strongest_detection_is_offered_first(tmp_path):
    project, track, candidate = _reviewable(tmp_path, "Strongest First")

    suggestions = client.get(f"/frames/{candidate['frame_id']}/suggestions").json()

    confidences = [s["detector_confidence"] for s in suggestions]
    assert confidences == sorted(confidences, reverse=True)


def test_a_suggestion_is_not_an_annotation(tmp_path):
    """Nothing is written until the user saves, which is what makes
    correcting one the same gesture as accepting it."""
    project, track, candidate = _reviewable(tmp_path, "Suggestions Are Not Labels")

    client.get(f"/frames/{candidate['frame_id']}/suggestions")

    assert client.get(f"/frames/{candidate['frame_id']}/annotations").json() == []
    assert client.get(f"/frames/{candidate['frame_id']}").json()["status"] != "labeled"


def test_correcting_a_suggestion_is_what_lands_in_the_dataset(tmp_path):
    """The model got the box slightly wrong; the human moves it and
    saves; the dataset gets the human's box."""
    project, track, candidate = _reviewable(tmp_path, "Correcting A Suggestion")
    corrected = [5.0, 5.0, 60.0, 50.0]

    client.put(
        f"/frames/{candidate['frame_id']}/annotations",
        json={"annotations": [{"class_id": PLATE_CLASS, "bbox_json": corrected}]},
    )

    saved = client.get(f"/frames/{candidate['frame_id']}/annotations").json()
    assert [a["bbox_json"] for a in saved] == [corrected]
    assert client.get(f"/frames/{candidate['frame_id']}").json()["status"] == "labeled"


def test_a_frame_with_two_detections_is_not_finished_by_accepting_one(tmp_path):
    """Accepting a track is judging one detection, not looking at a
    whole frame. Marking it finished would hide the export's
    partially-labelled warning and teach the model that the second
    plate is background.
    """
    from app.db.models.frame_candidate import FrameCandidate
    from app.db.session import SessionLocal

    project, track, candidate = _reviewable(tmp_path, "Two Plates One Frame")

    # A second detection on the same frame, as a second vehicle would
    # leave behind.
    with SessionLocal() as db:
        db.add(
            FrameCandidate(
                track_id=track["id"],
                frame_id=candidate["frame_id"],
                frame_index=candidate["frame_index"],
                timestamp_ms=candidate["timestamp_ms"],
                bbox_json=[90.0, 10.0, 120.0, 40.0],
                detector_class="car",
                detector_confidence=0.7,
                blur_score=0.5,
                sharpness_score=80.0,
                area_ratio=0.02,
                flags_json={},
            )
        )
        db.commit()

    _accept(track, candidate)

    assert client.get(f"/frames/{candidate['frame_id']}").json()["status"] != "labeled"
