from pathlib import Path

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


def _upload_video(project: dict, body: bytes, filename: str):
    return client.post(
        f"/projects/{project['id']}/sources/upload",
        params={"filename": filename},
        content=body,
        headers={"Content-Type": "video/mp4"},
    )


def test_a_video_uploaded_from_this_computer_becomes_a_source_like_an_imported_one(tmp_path):
    project = _create_project("Upload Project")
    original = create_synthetic_video(tmp_path / "gate.mp4", frame_count=60, fps=30.0)

    response = _upload_video(project, original.read_bytes(), "C:\\Users\\me\\Videos\\gate.mp4")

    assert response.status_code == 201, response.text
    body = response.json()
    assert (body["type"], body["fps"], body["frame_count"]) == ("video", 30.0, 60)
    stored = Path(body["path_or_uri"])
    # Only the file's own name, in the project's source folder.
    assert stored.parent == Path(project["workspace_path"]) / "source"
    assert stored.name == "gate.mp4"
    assert stored.read_bytes() == original.read_bytes()

    # The same name twice is two files, not one overwritten.
    second = _upload_video(project, original.read_bytes(), "gate.mp4").json()
    assert second["path_or_uri"] != body["path_or_uri"]


def test_an_upload_that_is_not_a_video_is_refused_and_not_kept(tmp_path):
    project = _create_project("Upload Refused Project")

    response = _upload_video(project, b"definitely not a video", "notes.mp4")

    assert response.status_code == 400
    assert list((Path(project["workspace_path"]) / "source").glob("*")) == []


def test_an_upload_named_dot_dot_stays_in_the_source_folder(tmp_path):
    project = _create_project("Upload Name Project")
    original = create_synthetic_video(tmp_path / "clip.mp4", frame_count=30, fps=30.0)

    response = _upload_video(project, original.read_bytes(), "..")

    assert response.status_code == 201, response.text
    assert Path(response.json()["path_or_uri"]).parent == Path(project["workspace_path"]) / "source"
