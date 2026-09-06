from fastapi.testclient import TestClient

from app.main import app
from app.ml.factory import get_default_detector
from tests.stub_detector import StubDetector
from tests.video_factory import create_synthetic_video

client = TestClient(app)


def _create_project(name: str = "Tracks API Project") -> dict:
    return client.post("/projects", json={"name": name}).json()


def test_process_source_creates_tracks_and_timeline_is_browsable(tmp_path):
    app.dependency_overrides[get_default_detector] = lambda: StubDetector()
    try:
        project = _create_project()
        video = create_synthetic_video(tmp_path / "clip.mp4", frame_count=30, fps=10.0)
        source = client.post(f"/projects/{project['id']}/sources", json={"path": str(video)}).json()

        response = client.post(
            f"/projects/{project['id']}/sources/{source['id']}/process",
            json={"sampling_config": {"target_fps": 5.0}},
        )
        assert response.status_code == 201
        body = response.json()
        assert body["run"]["status"] == "completed"
        assert body["run"]["detector_version"] == "stub-detector-v1"
        assert len(body["tracks"]) == 1
        track = body["tracks"][0]
        assert track["bucket"] in {"BEST_DETECTION", "HARD", "FAILED"}
        assert track["review_status"] == "unreviewed"

        listed = client.get(f"/projects/{project['id']}/tracks").json()
        assert any(t["id"] == track["id"] for t in listed)

        timeline = client.get(f"/tracks/{track['id']}").json()
        assert timeline["track"]["id"] == track["id"]
        frame_indices = [f["frame_index"] for f in timeline["frames"]]
        assert frame_indices == sorted(frame_indices)
        assert len(timeline["frames"]) == 14
        assert timeline["frames"][0]["detector_class"] == "car"
    finally:
        app.dependency_overrides.pop(get_default_detector, None)


def test_process_with_no_detections_returns_empty_tracks(tmp_path):
    app.dependency_overrides[get_default_detector] = lambda: StubDetector(box_per_frame={})
    try:
        project = _create_project("Empty Tracks Project")
        video = create_synthetic_video(tmp_path / "clip.mp4", frame_count=10, fps=10.0)
        source = client.post(f"/projects/{project['id']}/sources", json={"path": str(video)}).json()

        response = client.post(
            f"/projects/{project['id']}/sources/{source['id']}/process",
            json={"sampling_config": {"target_fps": 5.0}},
        )
        assert response.status_code == 201
        assert response.json()["tracks"] == []
    finally:
        app.dependency_overrides.pop(get_default_detector, None)


def test_get_unknown_track_returns_404():
    response = client.get("/tracks/does-not-exist")
    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


def test_process_unknown_source_returns_404():
    project = _create_project()
    response = client.post(
        f"/projects/{project['id']}/sources/does-not-exist/process",
        json={"sampling_config": {"target_fps": 5.0}},
    )
    assert response.status_code == 404
