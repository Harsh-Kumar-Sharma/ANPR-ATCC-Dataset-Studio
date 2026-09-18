from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app
from tests.job_execution import process_source_sync, tracks_for_run
from tests.stub_detector import StubDetector
from tests.video_factory import create_synthetic_video

client = TestClient(app)


def _processed_source(tmp_path, name: str, runs: int = 1) -> tuple[dict, dict, list[str]]:
    project = client.post("/projects", json={"name": name}).json()
    video = create_synthetic_video(tmp_path / f"{name}.mp4", frame_count=20, fps=10.0)
    source = client.post(f"/projects/{project['id']}/sources", json={"path": str(video)}).json()
    run_ids = []
    for _ in range(runs):
        submitted = process_source_sync(client, project["id"], source["id"], StubDetector())
        run_ids.append(submitted["run_id"])
    return project, source, run_ids


def test_video_is_streamed_with_range_support(tmp_path):
    project, source, _ = _processed_source(tmp_path, "Video Stream Project")
    url = f"/projects/{project['id']}/sources/{source['id']}/video"
    file_size = Path(source["path_or_uri"]).stat().st_size

    full = client.get(url)
    assert full.status_code == 200
    assert full.headers["content-type"].startswith("video/mp4")
    assert full.headers["accept-ranges"] == "bytes"
    assert len(full.content) == file_size

    # The <video> element seeks with Range requests; without 206 support
    # scrubbing would re-download from the start every time.
    partial = client.get(url, headers={"Range": "bytes=0-99"})
    assert partial.status_code == 206
    assert len(partial.content) == 100
    assert partial.headers["content-range"] == f"bytes 0-99/{file_size}"


def test_live_stream_source_has_no_playable_file(tmp_path):
    from app.db.models.source import Source
    from app.db.session import SessionLocal

    project = client.post("/projects", json={"name": "RTSP Not Playable Project"}).json()
    db = SessionLocal()
    try:
        source = Source(
            project_id=project["id"],
            type="rtsp",
            path_or_uri="rtsp://camera.example/stream",
            fps=10.0,
            width=0,
            height=0,
            duration_ms=0,
            frame_count=0,
        )
        db.add(source)
        db.commit()
        source_id = source.id
    finally:
        db.close()

    response = client.get(f"/projects/{project['id']}/sources/{source_id}/video")
    assert response.status_code == 400
    assert response.json()["code"] == "source_not_playable"


def test_detections_are_grouped_by_frame_in_time_order(tmp_path):
    project, source, run_ids = _processed_source(tmp_path, "Detections Project")
    response = client.get(f"/projects/{project['id']}/sources/{source['id']}/detections")
    assert response.status_code == 200
    body = response.json()

    assert body["run_id"] == run_ids[0]
    assert (body["width"], body["height"]) == (64, 48)
    assert len(body["runs"]) == 1

    tracks = client.get(f"/projects/{project['id']}/tracks").json()
    assert body["runs"][0]["track_count"] == len(tracks)
    track_ids = {t["id"] for t in tracks}

    frames = body["frames"]
    assert frames, "a processed source should have detections"
    timestamps = [f["timestamp_ms"] for f in frames]
    assert timestamps == sorted(timestamps)

    total_boxes = 0
    for frame in frames:
        for box in frame["boxes"]:
            total_boxes += 1
            assert box["track_id"] in track_ids
            x1, y1, x2, y2 = box["bbox"]
            assert 0 <= x1 < x2 and 0 <= y1 < y2, "bbox should be full-frame xyxy"
            assert box["detector_class"] == "car"
            assert 0.0 < box["confidence"] <= 1.0

    # Every persisted frame candidate is represented exactly once.
    candidates = sum(len(client.get(f"/tracks/{t['id']}").json()["frames"]) for t in tracks)
    assert total_boxes == candidates


def test_detections_default_to_the_newest_run_and_never_mix_runs(tmp_path):
    project, source, run_ids = _processed_source(tmp_path, "Two Run Detections Project", runs=2)
    base = f"/projects/{project['id']}/sources/{source['id']}/detections"

    newest = client.get(base).json()
    assert newest["run_id"] == run_ids[1]
    assert [r["id"] for r in newest["runs"]] == [run_ids[1], run_ids[0]]

    older = client.get(base, params={"run_id": run_ids[0]}).json()
    assert older["run_id"] == run_ids[0]
    older_tracks = {t["id"] for t in client.get(f"/projects/{project['id']}/tracks", params={"run_id": run_ids[0]}).json()}
    shown = {box["track_id"] for frame in older["frames"] for box in frame["boxes"]}
    assert shown and shown <= older_tracks, "boxes from another run leaked into the overlay"


def test_unprocessed_source_has_no_detections(tmp_path):
    project = client.post("/projects", json={"name": "Unprocessed Detections Project"}).json()
    video = create_synthetic_video(tmp_path / "fresh.mp4", frame_count=10, fps=10.0)
    source = client.post(f"/projects/{project['id']}/sources", json={"path": str(video)}).json()

    body = client.get(f"/projects/{project['id']}/sources/{source['id']}/detections").json()
    assert body["run_id"] is None
    assert body["runs"] == []
    assert body["frames"] == []


def test_run_from_a_different_source_is_rejected(tmp_path):
    project_a, source_a, _ = _processed_source(tmp_path, "Run Owner A")
    _, _, run_ids_b = _processed_source(tmp_path, "Run Owner B")

    response = client.get(
        f"/projects/{project_a['id']}/sources/{source_a['id']}/detections", params={"run_id": run_ids_b[0]}
    )
    assert response.status_code == 404
