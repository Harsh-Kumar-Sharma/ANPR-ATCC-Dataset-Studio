from fastapi.testclient import TestClient

from app.main import app
from tests.job_execution import process_source_sync, tracks_for_run
from tests.stub_detector import StubDetector
from tests.video_factory import create_synthetic_video

client = TestClient(app)


def _create_project(name: str = "Tracks API Project") -> dict:
    return client.post("/projects", json={"name": name}).json()


def test_process_source_creates_tracks_and_timeline_is_browsable(tmp_path):
    project = _create_project()
    video = create_synthetic_video(tmp_path / "clip.mp4", frame_count=30, fps=10.0)
    source = client.post(f"/projects/{project['id']}/sources", json={"path": str(video)}).json()

    submitted = process_source_sync(client, project["id"], source["id"], StubDetector())
    assert submitted["job"]["type"] == "detect"

    run = client.get(f"/processing-runs/{submitted['run_id']}").json()
    assert run["status"] == "completed"
    assert run["detector_version"] == "stub-detector-v1"

    tracks = tracks_for_run(client, project["id"], submitted["run_id"])
    assert len(tracks) == 1
    track = tracks[0]
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


def test_process_with_no_detections_returns_empty_tracks(tmp_path):
    project = _create_project("Empty Tracks Project")
    video = create_synthetic_video(tmp_path / "clip.mp4", frame_count=10, fps=10.0)
    source = client.post(f"/projects/{project['id']}/sources", json={"path": str(video)}).json()

    submitted = process_source_sync(client, project["id"], source["id"], StubDetector(box_per_frame={}))

    assert tracks_for_run(client, project["id"], submitted["run_id"]) == []


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


def test_process_source_conflicts_when_a_run_is_already_in_progress(tmp_path):
    """A detect+track run on real footage can take 1-3 minutes. Without
    this guard, a second click (or a reload + re-click) during that
    window used to reach the DB layer and crash with a raw
    ``sqlite3.OperationalError: database is locked`` instead of a clear
    "already running" message - see docs/HANDOFF.md."""
    from app.db.models.processing_run import ProcessingRun
    from app.db.session import SessionLocal

    project = _create_project("Conflict Project")
    video = create_synthetic_video(tmp_path / "clip.mp4", frame_count=10, fps=10.0)
    source = client.post(f"/projects/{project['id']}/sources", json={"path": str(video)}).json()

    db = SessionLocal()
    try:
        db.add(ProcessingRun(source_id=source["id"], sampling_config={"target_fps": 5.0}, status="running"))
        db.commit()
    finally:
        db.close()

    response = client.post(
        f"/projects/{project['id']}/sources/{source['id']}/process",
        json={"sampling_config": {"target_fps": 5.0}},
    )
    assert response.status_code == 409
    assert response.json()["code"] == "processing_already_running"

    sources = client.get(f"/projects/{project['id']}/sources").json()
    assert next(s for s in sources if s["id"] == source["id"])["is_processing"] is True
