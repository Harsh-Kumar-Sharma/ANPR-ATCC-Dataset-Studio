from fastapi.testclient import TestClient

from app.main import app
from tests.video_factory import create_synthetic_video

client = TestClient(app)


def _create_project(name: str = "Sampling Project") -> dict:
    return client.post("/projects", json={"name": name}).json()


def test_import_source_probes_and_copies_video_into_workspace(tmp_path):
    project = _create_project()
    original = create_synthetic_video(tmp_path / "incoming" / "clip.mp4", frame_count=60, fps=30.0)

    response = client.post(f"/projects/{project['id']}/sources", json={"path": str(original)})
    assert response.status_code == 201
    body = response.json()

    assert body["fps"] == 30.0
    assert body["frame_count"] == 60
    assert body["width"] == 64
    assert body["height"] == 48
    assert body["project_id"] == project["id"]

    # Source is copied into the project's own workspace, not referenced
    # from the original (possibly transient) import location.
    assert project["workspace_path"] in body["path_or_uri"]
    assert original.name in body["path_or_uri"]


def test_import_missing_source_returns_error():
    project = _create_project()
    response = client.post(f"/projects/{project['id']}/sources", json={"path": "C:/does/not/exist.mp4"})
    assert response.status_code == 400
    assert response.json()["code"] == "source_not_found"


def test_sample_source_produces_reproducible_timestamps(tmp_path):
    project = _create_project("Reproducibility Project")
    original = create_synthetic_video(tmp_path / "clip.mp4", frame_count=90, fps=30.0)
    source = client.post(f"/projects/{project['id']}/sources", json={"path": str(original)}).json()

    first = client.post(
        f"/projects/{project['id']}/sources/{source['id']}/sample",
        json={"sampling_config": {"target_fps": 5.0}},
    )
    second = client.post(
        f"/projects/{project['id']}/sources/{source['id']}/sample",
        json={"sampling_config": {"target_fps": 5.0}},
    )

    assert first.status_code == 201
    assert second.status_code == 201

    first_frames = first.json()["sampled_frames"]
    second_frames = second.json()["sampled_frames"]

    assert first_frames == second_frames
    assert len(first_frames) == 15  # 90 frames at native 30fps, sampled every 6th frame
    assert first_frames[0] == {"frame_index": 0, "timestamp_ms": 0}
    assert first_frames[1] == {"frame_index": 6, "timestamp_ms": 200}

    run = first.json()["run"]
    assert run["status"] == "completed"
    assert run["sampled_frame_count"] == 15


def test_sample_unknown_source_returns_404():
    project = _create_project()
    response = client.post(
        f"/projects/{project['id']}/sources/does-not-exist/sample",
        json={"sampling_config": {"target_fps": 5.0}},
    )
    assert response.status_code == 404
