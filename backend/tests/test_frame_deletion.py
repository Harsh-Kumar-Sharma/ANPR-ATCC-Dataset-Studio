"""Deleting a frame for good, as distinct from skipping it.

Skipping keeps a frame so the decision can be undone. Some frames do
not deserve that: blank ones, blurred ones, ones where the detector
fired on nothing. Those are disk being spent on rubbish.

The one thing deletion must not do is rewrite a dataset version that
has already been exported. Those are immutable, and something may have
trained on one.
"""

from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.ml.types import Detection
from tests.job_execution import process_source_sync
from tests.video_factory import create_synthetic_video

client = TestClient(app)

WIDTH, HEIGHT = 128, 96
CAR_CLASS = 4


class _OneCarDetector:
    model_version = "one-car-stub-v1"
    class_names = {0: "car"}

    def detect(self, frame):
        return [Detection(bbox_xyxy=(20.0, 20.0, 80.0, 70.0), class_id=0, confidence=0.9)]


def _project_with_frames(tmp_path, name: str, frame_count: int = 8):
    project = client.post("/projects", json={"name": name}).json()
    video = create_synthetic_video(tmp_path / "clip.mp4", frame_count=frame_count, fps=10.0, width=WIDTH, height=HEIGHT)
    source = client.post(f"/projects/{project['id']}/sources", json={"path": str(video)}).json()
    process_source_sync(client, project["id"], source["id"], _OneCarDetector(), target_fps=10.0)
    frames = client.get(f"/projects/{project['id']}/frames").json()
    assert frames
    return project, frames


def _label(frame: dict, class_id: int = CAR_CLASS):
    response = client.put(
        f"/frames/{frame['id']}/annotations",
        json={"annotations": [{"class_id": class_id, "bbox_json": [10.0, 10.0, 70.0, 60.0]}]},
    )
    assert response.status_code == 200, response.text


def _track_count(project: dict) -> int:
    """This project's tracks only - the table is shared with every
    other test in the file."""
    from app.db.models import ProcessingRun, Source, Track
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        return (
            db.query(Track)
            .join(ProcessingRun, Track.run_id == ProcessingRun.id)
            .join(Source, ProcessingRun.source_id == Source.id)
            .filter(Source.project_id == project["id"])
            .count()
        )


def _progress(project: dict) -> dict:
    return client.get(f"/projects/{project['id']}/frames/progress").json()


# --- deleting -----------------------------------------------------------------


def test_deleting_a_frame_removes_it_from_the_queue(tmp_path):
    project, frames = _project_with_frames(tmp_path, "Delete Frame")
    before = len(frames)

    response = client.delete(f"/frames/{frames[0]['id']}")

    assert response.status_code == 200, response.text
    assert client.get(f"/frames/{frames[0]['id']}").status_code == 404
    assert len(client.get(f"/projects/{project['id']}/frames").json()) == before - 1


def test_deleting_takes_its_labels_and_detections_with_it(tmp_path):
    from app.db.models import Annotation, FrameCandidate
    from app.db.session import SessionLocal

    project, frames = _project_with_frames(tmp_path, "Frame Cascade")
    _label(frames[0])
    frame_id = frames[0]["id"]

    with SessionLocal() as db:
        annotation_ids = [a for a in db.scalars(select(Annotation.id).where(Annotation.frame_id == frame_id))]
        candidate_ids = [c for c in db.scalars(select(FrameCandidate.id).where(FrameCandidate.frame_id == frame_id))]
        assert annotation_ids and candidate_ids

    client.delete(f"/frames/{frame_id}")

    with SessionLocal() as db:
        assert db.query(Annotation).filter(Annotation.id.in_(annotation_ids)).count() == 0
        assert db.query(FrameCandidate).filter(FrameCandidate.id.in_(candidate_ids)).count() == 0


def test_a_track_left_with_no_detections_goes_too(tmp_path):
    """Otherwise the track browser fills with tracks that have nothing
    in them, and the evaluation report counts them."""
    project, frames = _project_with_frames(tmp_path, "Emptied Track", frame_count=2)
    assert _track_count(project) >= 1

    for frame in client.get(f"/projects/{project['id']}/frames").json():
        client.delete(f"/frames/{frame['id']}")

    assert _track_count(project) == 0


def test_a_track_that_still_has_other_frames_survives(tmp_path):
    """One frame of a track is not the track."""
    project, frames = _project_with_frames(tmp_path, "Surviving Track", frame_count=8)

    client.delete(f"/frames/{frames[0]['id']}")

    assert _track_count(project) >= 1


def test_deleting_removes_the_image_from_disk(tmp_path):
    """The whole point. A frame kept only as a row costs nothing; the
    decoded image is what fills a disk."""
    project, frames = _project_with_frames(tmp_path, "Frame On Disk")
    # Fetching the image is what decodes and stores it.
    assert client.get(f"/frames/{frames[0]['id']}/image").status_code == 200

    from app.db.models import Frame
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        image_path = Path(db.get(Frame, frames[0]["id"]).image_path)
    assert image_path.is_file()

    body = client.delete(f"/frames/{frames[0]['id']}").json()

    assert body["image_removed"] is True
    assert not image_path.exists()


def test_deleting_a_frame_that_was_never_decoded_is_fine(tmp_path):
    project, frames = _project_with_frames(tmp_path, "Never Decoded")

    body = client.delete(f"/frames/{frames[0]['id']}").json()

    assert body["image_removed"] is False


def test_the_queue_counts_drop(tmp_path):
    project, frames = _project_with_frames(tmp_path, "Queue Counts")
    before = _progress(project)

    client.delete(f"/frames/{frames[0]['id']}")

    after = _progress(project)
    assert after["total"] == before["total"] - 1


def test_a_deleted_label_leaves_the_balance(tmp_path):
    project, frames = _project_with_frames(tmp_path, "Balance Drops")
    _label(frames[0])
    assert client.get(f"/projects/{project['id']}/label-balance").json()["total_boxes"] == 1

    client.delete(f"/frames/{frames[0]['id']}")

    assert client.get(f"/projects/{project['id']}/label-balance").json()["total_boxes"] == 0


# --- what it refuses ----------------------------------------------------------


def test_a_frame_in_an_exported_dataset_is_not_deleted(tmp_path):
    """A version already exported is immutable and may have been
    trained on. Deleting the frame under it would make the manifest
    describe something that no longer exists."""
    project, frames = _project_with_frames(tmp_path, "Exported Frame")
    _label(frames[0])
    created = client.post(f"/projects/{project['id']}/dataset-versions", json={})
    assert created.status_code == 201, created.text

    response = client.delete(f"/frames/{frames[0]['id']}")

    assert response.status_code == 409
    assert response.json()["code"] == "frame_exported"
    assert "v1" in response.json()["message"]
    assert client.get(f"/frames/{frames[0]['id']}").status_code == 200


def test_an_unexported_frame_of_an_exported_project_is_still_deletable(tmp_path):
    """Only the frames actually in a version are held."""
    project, frames = _project_with_frames(tmp_path, "Partly Exported")
    _label(frames[0])
    client.post(f"/projects/{project['id']}/dataset-versions", json={})

    assert client.delete(f"/frames/{frames[1]['id']}").status_code == 200


def test_deleting_an_unknown_frame_is_a_404():
    assert client.delete("/frames/does-not-exist").status_code == 404
