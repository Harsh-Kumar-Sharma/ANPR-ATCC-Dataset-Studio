from fastapi.testclient import TestClient

from app.main import app
from tests.job_execution import process_source_sync, tracks_for_run
from tests.stub_detector import StubDetector
from tests.video_factory import create_synthetic_video

client = TestClient(app)


def _setup_run(tmp_path, name: str):
    project = client.post("/projects", json={"name": name}).json()
    video = create_synthetic_video(tmp_path / f"{name}.mp4", frame_count=30, fps=10.0)
    source = client.post(f"/projects/{project['id']}/sources", json={"path": str(video)}).json()
    submitted = process_source_sync(client, project["id"], source["id"], StubDetector())
    run = client.get(f"/processing-runs/{submitted['run_id']}").json()
    tracks = tracks_for_run(client, project["id"], submitted["run_id"])
    return {"project": project, "source": source, "run": run, "track": tracks[0]}


def test_freeze_source_requires_positive_count(tmp_path):
    ctx = _setup_run(tmp_path, "Freeze Validation Project")
    project, source = ctx["project"], ctx["source"]

    bad = client.put(f"/projects/{project['id']}/sources/{source['id']}/freeze", json={"ground_truth_vehicle_count": 0})
    assert bad.status_code == 422

    good = client.put(f"/projects/{project['id']}/sources/{source['id']}/freeze", json={"ground_truth_vehicle_count": 5})
    assert good.status_code == 200
    assert good.json()["is_frozen"] is True
    assert good.json()["ground_truth_vehicle_count"] == 5


def test_evaluation_without_frozen_source_has_null_recall(tmp_path):
    ctx = _setup_run(tmp_path, "Unfrozen Evaluation Project")
    response = client.get(f"/processing-runs/{ctx['run']['id']}/evaluation")
    assert response.status_code == 200
    body = response.json()
    assert body["is_frozen_validation_clip"] is False
    assert body["detection_recall"] is None


def test_evaluation_with_frozen_source_computes_recall(tmp_path):
    ctx = _setup_run(tmp_path, "Frozen Evaluation Project")
    project, source, run, track = ctx["project"], ctx["source"], ctx["run"], ctx["track"]

    client.put(f"/projects/{project['id']}/sources/{source['id']}/freeze", json={"ground_truth_vehicle_count": 2})

    frame_id = client.get(f"/tracks/{track['id']}").json()["frames"][0]["id"]
    client.put(f"/tracks/{track['id']}/review", json={"frame_candidate_id": frame_id, "decision": "accepted", "class_id": 4})

    response = client.get(f"/processing-runs/{run['id']}/evaluation")
    body = response.json()
    assert body["is_frozen_validation_clip"] is True
    assert body["ground_truth_vehicle_count"] == 2
    assert body["track_counts"]["confirmed"] == 1
    assert body["detection_recall"] == 0.5  # 1 confirmed / 2 ground truth


def test_evaluation_class_distribution_reflects_accepted_class(tmp_path):
    ctx = _setup_run(tmp_path, "Class Distribution Project")
    track, run = ctx["track"], ctx["run"]
    frame_id = client.get(f"/tracks/{track['id']}").json()["frames"][0]["id"]
    client.put(f"/tracks/{track['id']}/review", json={"frame_candidate_id": frame_id, "decision": "accepted", "class_id": 4})

    body = client.get(f"/processing-runs/{run['id']}/evaluation").json()
    assert body["class_distribution"] == {"Car/Jeep/Van": 1}


def test_evaluation_failure_gallery_includes_failed_tracks(tmp_path):
    ctx = _setup_run(tmp_path, "Failure Gallery Project")
    track, run = ctx["track"], ctx["run"]
    frame_id = client.get(f"/tracks/{track['id']}").json()["frames"][0]["id"]
    client.put(f"/tracks/{track['id']}/review", json={"frame_candidate_id": frame_id, "decision": "failed"})

    body = client.get(f"/processing-runs/{run['id']}/evaluation").json()
    assert len(body["failure_gallery"]) == 1
    assert body["failure_gallery"][0]["track_id"] == track["id"]
    assert body["failure_gallery"][0]["review_status"] == "failed"
    assert body["failure_gallery"][0]["representative_frame_id"] == frame_id


def test_evaluation_ocr_metrics_need_both_sides(tmp_path):
    """A plate a human recorded with no model reading to compare it
    against says nothing about the model, so it is not counted. This
    used to call the removed ocr-selection endpoint, ignore its status,
    and then assert a zero that was true because nothing had happened."""
    ctx = _setup_run(tmp_path, "OCR Metrics Project")
    track, run = ctx["track"], ctx["run"]
    frame_id = client.get(f"/tracks/{track['id']}").json()["frames"][0]["id"]

    reviewed = client.put(
        f"/tracks/{track['id']}/review",
        json={"frame_candidate_id": frame_id, "decision": "accepted", "class_id": 4},
    )
    assert reviewed.status_code == 200, reviewed.text
    recorded = client.put(f"/tracks/{track['id']}/plate-text", json={"plate_text": "MH12AB1234"})
    assert recorded.status_code == 200, recorded.text
    assert recorded.json()["attributes"]["plate_text"] == "MH12AB1234"

    body = client.get(f"/processing-runs/{run['id']}/evaluation").json()

    assert body["ocr_metrics"]["tracks_with_ocr"] == 0, "no OCR was ever run on this track"
    assert body["ocr_metrics"]["agreement_rate"] is None


def test_evaluation_for_unknown_run_returns_404():
    response = client.get("/processing-runs/does-not-exist/evaluation")
    assert response.status_code == 404
