"""Clearing out the detections nobody wanted.

Reviewing is: look at what the model found, accept the ones worth
keeping, and then be left with a list of a hundred you do not want.
Deleting those one at a time is tidying, not reviewing.
"""

from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app
from app.ml.types import Detection
from tests.job_execution import process_source_sync
from tests.video_factory import create_synthetic_video

client = TestClient(app)

WIDTH, HEIGHT = 128, 96
PLATE_CLASS = 4


class _JumpingPlateDetector:
    """A plate, which is what this feature exists for.

    The box lands somewhere different every frame, as a number plate
    at a real frame rate does. The tracker cannot follow it, so every
    sighting becomes its own single-frame detection - which is
    exactly the list of a hundred that needs clearing out.
    """

    model_version = "jumping-plate-stub-v1"
    class_names = {0: "plate"}

    def __init__(self) -> None:
        self._seen = 0

    def detect(self, frame):
        x = 5.0 + (self._seen * 37) % 60
        y = 5.0 + (self._seen * 23) % 50
        self._seen += 1
        return [Detection(bbox_xyxy=(x, y, x + 20.0, y + 10.0), class_id=0, confidence=0.9)]


def _reviewed(tmp_path, name: str, frame_count: int = 8) -> tuple[dict, list[dict]]:
    project = client.post("/projects", json={"name": name}).json()
    video = create_synthetic_video(
        tmp_path / "clip.mp4", frame_count=frame_count, fps=10.0, width=WIDTH, height=HEIGHT
    )
    source = client.post(f"/projects/{project['id']}/sources", json={"path": str(video)}).json()
    process_source_sync(client, project["id"], source["id"], _JumpingPlateDetector(), target_fps=10.0)
    return project, client.get(f"/projects/{project['id']}/tracks").json()


def _decide(track: dict, decision: str) -> None:
    candidate = client.get(f"/tracks/{track['id']}").json()["frames"][0]
    response = client.put(
        f"/tracks/{track['id']}/review",
        json={
            "decision": decision,
            "frame_candidate_id": candidate["id"],
            "class_id": PLATE_CLASS,
            "bbox_json": candidate["bbox_json"],
        },
    )
    assert response.status_code == 200, response.text


def _tracks(project: dict) -> list[dict]:
    return client.get(f"/projects/{project['id']}/tracks").json()


def _sweep(project: dict) -> dict:
    response = client.post(f"/projects/{project['id']}/tracks/sweep")
    assert response.status_code == 200, response.text
    return response.json()


# --- what it says before it does anything -------------------------------------


def test_the_preview_says_what_would_go(tmp_path):
    project, tracks = _reviewed(tmp_path, "Sweep Preview")
    _decide(tracks[0], "accepted")

    preview = client.get(f"/projects/{project['id']}/tracks/sweep").json()

    assert preview["kept"] == 1
    assert preview["deleted"] == len(tracks) - 1


def test_a_preview_deletes_nothing(tmp_path):
    project, tracks = _reviewed(tmp_path, "Preview Is Safe")

    client.get(f"/projects/{project['id']}/tracks/sweep")

    assert len(_tracks(project)) == len(tracks)


# --- what goes, and what stays ------------------------------------------------


def test_what_was_accepted_stays_and_the_rest_goes(tmp_path):
    project, tracks = _reviewed(tmp_path, "Sweep Runs")
    _decide(tracks[0], "accepted")

    done = _sweep(project)

    assert done["kept"] == 1
    assert [t["id"] for t in _tracks(project)] == [tracks[0]["id"]]


def test_a_track_flagged_hard_is_kept_too(tmp_path):
    """Someone marked it difficult, which is a reason to keep it for
    training, not to throw it away."""
    project, tracks = _reviewed(tmp_path, "Hard Is Kept")
    _decide(tracks[0], "hard")

    _sweep(project)

    assert [t["id"] for t in _tracks(project)] == [tracks[0]["id"]]


def test_a_track_marked_failed_goes(tmp_path):
    """Failed is a human saying the detection is wrong."""
    project, tracks = _reviewed(tmp_path, "Failed Goes")
    _decide(tracks[0], "accepted")
    _decide(tracks[1], "failed")

    _sweep(project)

    surviving = [t["id"] for t in _tracks(project)]
    assert tracks[1]["id"] not in surviving


def test_sweeping_with_nothing_accepted_clears_the_lot(tmp_path):
    project, tracks = _reviewed(tmp_path, "Nothing Accepted")

    done = _sweep(project)

    assert done["deleted"] == len(tracks)
    assert _tracks(project) == []


# --- the frames underneath ----------------------------------------------------


def test_frames_left_holding_nothing_go_too(tmp_path):
    """They are a decoded image each, and nothing points at them."""
    project, tracks = _reviewed(tmp_path, "Frames Go Too")
    before = len(client.get(f"/projects/{project['id']}/frames").json())
    _decide(tracks[0], "accepted")

    done = _sweep(project)

    after = len(client.get(f"/projects/{project['id']}/frames").json())
    assert done["frames_deleted"] > 0
    assert after < before


def test_a_frame_someone_labelled_by_hand_is_never_swept(tmp_path):
    """The one thing that must survive tidying."""
    project, tracks = _reviewed(tmp_path, "Hand Labelled Survives")
    frames = client.get(f"/projects/{project['id']}/frames").json()
    client.put(
        f"/frames/{frames[0]['id']}/annotations",
        json={"annotations": [{"class_id": PLATE_CLASS, "bbox_json": [1.0, 1.0, 9.0, 9.0]}]},
    )

    _sweep(project)

    surviving = [f["id"] for f in client.get(f"/projects/{project['id']}/frames").json()]
    assert frames[0]["id"] in surviving


def test_the_frame_of_an_accepted_detection_survives(tmp_path):
    project, tracks = _reviewed(tmp_path, "Accepted Frame Survives")
    candidate = client.get(f"/tracks/{tracks[0]['id']}").json()["frames"][0]
    _decide(tracks[0], "accepted")

    _sweep(project)

    surviving = [f["id"] for f in client.get(f"/projects/{project['id']}/frames").json()]
    assert candidate["frame_id"] in surviving


def test_the_images_are_removed_from_disk(tmp_path):
    """The frames are the expensive part - one decoded image each."""
    project, tracks = _reviewed(tmp_path, "Disk Is Freed")
    for frame in client.get(f"/projects/{project['id']}/frames").json():
        client.get(f"/frames/{frame['id']}/image")
    _decide(tracks[0], "accepted")

    done = _sweep(project)

    assert done["bytes_freed"] > 0
    decoded = Path(project["workspace_path"]) / "derived" / "frames"
    assert len(list(decoded.rglob("*.jpg"))) < len(tracks)


# --- what it refuses to touch -------------------------------------------------


def test_an_exported_detection_is_held_back(tmp_path):
    """A dataset version is the record of what a model was trained
    on, and it has to keep describing rows that exist."""
    project, tracks = _reviewed(tmp_path, "Exported Is Held")
    _decide(tracks[0], "accepted")
    _decide(tracks[1], "accepted")
    exported = client.post(f"/projects/{project['id']}/dataset-versions", json={})
    assert exported.status_code in (200, 201), exported.text
    # Now un-accept one that the version already names.
    _decide(tracks[1], "failed")

    done = _sweep(project)

    assert done["held"] >= 1
    assert tracks[1]["id"] in [t["id"] for t in _tracks(project)]


def test_another_project_is_not_swept(tmp_path):
    project, tracks = _reviewed(tmp_path, "Mine To Sweep")
    other, theirs = _reviewed(tmp_path / "other", "Not Mine")

    _sweep(project)

    assert len(_tracks(other)) == len(theirs)
