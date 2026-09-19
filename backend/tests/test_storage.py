"""Seeing what the app is holding, and getting it back.

Space is the user's to manage, but nothing in the app said where its
gigabytes went. This is the view that says, and the handful of actions
that reclaim.

The rule underneath all of it: nothing here may remove a label, an
annotation or a frame row. Only pixels that can be regenerated - a
decoded frame is a cache of the source video - and exports the user
names outright.
"""

from pathlib import Path

from fastapi.testclient import TestClient

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


def _project_with_frames(tmp_path, name: str):
    project = client.post("/projects", json={"name": name}).json()
    video = create_synthetic_video(tmp_path / "clip.mp4", frame_count=8, fps=10.0, width=WIDTH, height=HEIGHT)
    source = client.post(f"/projects/{project['id']}/sources", json={"path": str(video)}).json()
    process_source_sync(client, project["id"], source["id"], _OneCarDetector(), target_fps=10.0)
    frames = client.get(f"/projects/{project['id']}/frames").json()
    return project, source, frames


def _storage() -> dict:
    response = client.get("/storage")
    assert response.status_code == 200, response.text
    return response.json()


def _project_usage(project: dict) -> dict:
    return next(p for p in _storage()["projects"] if p["project_id"] == project["id"])


# --- seeing it ----------------------------------------------------------------


def test_storage_says_how_much_room_is_left_on_the_drive(tmp_path):
    body = _storage()

    assert body["free_bytes"] > 0
    assert body["total_bytes"] > body["free_bytes"]


def test_storage_breaks_a_project_down_by_kind(tmp_path):
    project, source, frames = _project_with_frames(tmp_path, "Storage Breakdown")
    client.get(f"/frames/{frames[0]['id']}/image")

    usage = _project_usage(project)

    assert usage["name"] == "Storage Breakdown"
    assert usage["source_videos_bytes"] > 0, "the copy made at import"
    assert usage["track_crops_bytes"] > 0, "one crop per detection"
    assert usage["frame_images_bytes"] > 0, "decoded because the canvas asked for it"
    assert usage["total_bytes"] >= usage["source_videos_bytes"]


def test_storage_lists_the_biggest_project_first(tmp_path):
    small = client.post("/projects", json={"name": "Small"}).json()
    big, _, _ = _project_with_frames(tmp_path, "Big")

    names = [p["project_id"] for p in _storage()["projects"]]

    assert names.index(big["id"]) < names.index(small["id"])


def test_storage_counts_job_files_that_belong_to_no_workspace(tmp_path):
    """Progress files and worker logs live outside every project, which
    is why nothing ever cleaned them up."""
    from app.services.jobs import runner

    runner.progress_path("storage-probe").parent.mkdir(parents=True, exist_ok=True)
    runner.progress_path("storage-probe").write_text("x" * 500, encoding="utf-8")

    assert _storage()["job_files_bytes"] > 0


def test_storage_finds_a_workspace_belonging_to_no_project(tmp_path):
    """Two of these were already on the real machine, left by projects
    deleted before there was a delete that cleaned up."""
    from app.core.config import get_settings

    orphan = Path(get_settings().workspace_root) / "not-a-project-at-all"
    (orphan / "derived").mkdir(parents=True, exist_ok=True)
    (orphan / "derived" / "junk.bin").write_text("x" * 1000, encoding="utf-8")

    body = _storage()

    found = [o for o in body["orphan_workspaces"] if o["path"].endswith("not-a-project-at-all")]
    assert found, body["orphan_workspaces"]
    assert found[0]["bytes"] >= 1000


# --- getting it back ----------------------------------------------------------


def test_clearing_decoded_frames_frees_space_and_keeps_the_labels(tmp_path):
    """A decoded frame is a cache of the source video. The label drawn
    on it is not."""
    project, source, frames = _project_with_frames(tmp_path, "Clear Frames")
    client.get(f"/frames/{frames[0]['id']}/image")
    client.put(
        f"/frames/{frames[0]['id']}/annotations",
        json={"annotations": [{"class_id": CAR_CLASS, "bbox_json": [10.0, 10.0, 70.0, 60.0]}]},
    )
    assert _project_usage(project)["frame_images_bytes"] > 0

    response = client.post("/storage/clear-frame-images", json={"project_id": project["id"]})

    assert response.status_code == 200, response.text
    assert response.json()["reclaimed_bytes"] > 0
    assert _project_usage(project)["frame_images_bytes"] == 0
    assert len(client.get(f"/projects/{project['id']}/frames").json()) == len(frames)
    assert client.get(f"/projects/{project['id']}/label-balance").json()["total_boxes"] == 1


def test_a_cleared_frame_is_decoded_again_when_it_is_next_opened(tmp_path):
    """Which is what makes clearing them safe rather than destructive."""
    project, source, frames = _project_with_frames(tmp_path, "Recover Frames")
    client.get(f"/frames/{frames[0]['id']}/image")

    client.post("/storage/clear-frame-images", json={"project_id": project["id"]})

    assert client.get(f"/frames/{frames[0]['id']}/image").status_code == 200
    assert _project_usage(project)["frame_images_bytes"] > 0


def test_clearing_can_be_narrowed_to_one_source(tmp_path):
    project = client.post("/projects", json={"name": "Clear One Source"}).json()
    kept_video = create_synthetic_video(tmp_path / "a.mp4", frame_count=6, fps=10.0, width=WIDTH, height=HEIGHT)
    kept = client.post(f"/projects/{project['id']}/sources", json={"path": str(kept_video)}).json()
    process_source_sync(client, project["id"], kept["id"], _OneCarDetector(), target_fps=10.0)
    cleared_video = create_synthetic_video(tmp_path / "b.mp4", frame_count=6, fps=10.0, width=WIDTH, height=HEIGHT)
    cleared = client.post(f"/projects/{project['id']}/sources", json={"path": str(cleared_video)}).json()
    process_source_sync(client, project["id"], cleared["id"], _OneCarDetector(), target_fps=10.0)

    for frame in client.get(f"/projects/{project['id']}/frames").json():
        client.get(f"/frames/{frame['id']}/image")

    client.post("/storage/clear-frame-images", json={"project_id": project["id"], "source_id": cleared["id"]})

    from app.db.models import Frame
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        assert all(f.image_path is None for f in db.query(Frame).filter(Frame.source_id == cleared["id"]))
        assert any(f.image_path is not None for f in db.query(Frame).filter(Frame.source_id == kept["id"]))


def test_clearing_job_files_removes_only_finished_ones(tmp_path):
    """A running worker is still writing to its progress file."""
    from app.db.models import Job
    from app.db.session import SessionLocal
    from app.services.jobs import runner

    project = client.post("/projects", json={"name": "Clear Job Files"}).json()
    with SessionLocal() as db:
        finished = Job(project_id=project["id"], type="detect", status="failed", params_json={})
        running = Job(project_id=project["id"], type="detect", status="running", params_json={})
        db.add_all([finished, running])
        db.commit()
        finished_id, running_id = finished.id, running.id

    runner.progress_path(finished_id).parent.mkdir(parents=True, exist_ok=True)
    for job_id in (finished_id, running_id):
        runner.log_path(job_id).write_text("x" * 400, encoding="utf-8")

    response = client.post("/storage/clear-job-files", json={})

    assert response.status_code == 200, response.text
    assert not runner.log_path(finished_id).exists()
    assert runner.log_path(running_id).is_file(), "its worker is still writing to it"


def test_removing_orphan_workspaces_leaves_real_ones_alone(tmp_path):
    from app.core.config import get_settings

    project, source, frames = _project_with_frames(tmp_path, "Keep My Workspace")
    live = Path(client.get(f"/projects/{project['id']}").json()["workspace_path"])
    orphan = Path(get_settings().workspace_root) / "abandoned-workspace"
    orphan.mkdir(parents=True, exist_ok=True)
    (orphan / "junk.bin").write_text("x" * 800, encoding="utf-8")

    response = client.post("/storage/remove-orphan-workspaces", json={})

    assert response.status_code == 200, response.text
    assert response.json()["reclaimed_bytes"] >= 800
    assert not orphan.exists()
    assert live.is_dir()


def test_a_dataset_version_can_be_deleted(tmp_path):
    project, source, frames = _project_with_frames(tmp_path, "Delete Export")
    client.put(
        f"/frames/{frames[0]['id']}/annotations",
        json={"annotations": [{"class_id": CAR_CLASS, "bbox_json": [10.0, 10.0, 70.0, 60.0]}]},
    )
    created = client.post(f"/projects/{project['id']}/dataset-versions", json={}).json()
    version_id = created["dataset_version"]["id"]
    export_dir = Path(client.get(f"/projects/{project['id']}").json()["workspace_path"]) / "exports" / "v1"
    assert export_dir.is_dir()

    response = client.delete(f"/dataset-versions/{version_id}")

    assert response.status_code == 200, response.text
    assert response.json()["reclaimed_bytes"] > 0
    assert not export_dir.exists()
    assert client.get(f"/projects/{project['id']}/dataset-versions").json() == []


def test_deleting_a_version_keeps_the_labels_it_was_made_from(tmp_path):
    """The export is a snapshot. Deleting it does not un-label
    anything - the same frames export again next time."""
    project, source, frames = _project_with_frames(tmp_path, "Version Not Labels")
    client.put(
        f"/frames/{frames[0]['id']}/annotations",
        json={"annotations": [{"class_id": CAR_CLASS, "bbox_json": [10.0, 10.0, 70.0, 60.0]}]},
    )
    created = client.post(f"/projects/{project['id']}/dataset-versions", json={}).json()

    client.delete(f"/dataset-versions/{created['dataset_version']['id']}")

    assert client.get(f"/projects/{project['id']}/label-balance").json()["total_boxes"] == 1
    assert client.post(f"/projects/{project['id']}/dataset-versions", json={}).status_code == 201


def test_deleting_a_version_releases_the_frames_it_was_holding(tmp_path):
    """A frame in an exported version cannot be deleted. Once the
    version goes, it can."""
    project, source, frames = _project_with_frames(tmp_path, "Version Releases Frames")
    client.put(
        f"/frames/{frames[0]['id']}/annotations",
        json={"annotations": [{"class_id": CAR_CLASS, "bbox_json": [10.0, 10.0, 70.0, 60.0]}]},
    )
    created = client.post(f"/projects/{project['id']}/dataset-versions", json={}).json()
    assert client.delete(f"/frames/{frames[0]['id']}").status_code == 409

    client.delete(f"/dataset-versions/{created['dataset_version']['id']}")

    assert client.delete(f"/frames/{frames[0]['id']}").status_code == 200


def test_reclaiming_works_while_a_job_is_running(tmp_path):
    """That is exactly when the disk fills, and exactly when stopping
    everything to tidy up costs the most."""
    from app.db.models import Job
    from app.db.session import SessionLocal

    project, source, frames = _project_with_frames(tmp_path, "Clear While Busy")
    client.get(f"/frames/{frames[0]['id']}/image")
    with SessionLocal() as db:
        db.add(Job(project_id=project["id"], type="detect", status="running", params_json={}))
        db.commit()

    response = client.post("/storage/clear-frame-images", json={"project_id": project["id"]})

    assert response.status_code == 200, response.text
    assert response.json()["reclaimed_bytes"] > 0
