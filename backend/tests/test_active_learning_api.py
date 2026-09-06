import numpy as np
from fastapi.testclient import TestClient

from app.main import app
from app.ml.factory import get_default_detector
from app.ml.types import Detection
from tests.video_factory import create_synthetic_video

client = TestClient(app)


class _ConfidenceDetector:
    """A detector with a fixed, caller-chosen confidence - lets tests
    control ordering, which the shared StubDetector's fixed 0.9
    confidence cannot."""

    model_version = "confidence-stub-v1"
    class_names = {0: "car"}

    def __init__(self, confidence: float) -> None:
        self._confidence = confidence

    def detect(self, frame: np.ndarray) -> list[Detection]:
        return [Detection(bbox_xyxy=(10, 10, 60, 40), class_id=0, confidence=self._confidence)]


def _create_track(tmp_path, name: str, confidence: float = 0.9) -> dict:
    app.dependency_overrides[get_default_detector] = lambda: _ConfidenceDetector(confidence)
    try:
        project = client.post("/projects", json={"name": name}).json()
        video = create_synthetic_video(tmp_path / f"{name}.mp4", frame_count=30, fps=10.0)
        source = client.post(f"/projects/{project['id']}/sources", json={"path": str(video)}).json()
        result = client.post(
            f"/projects/{project['id']}/sources/{source['id']}/process",
            json={"sampling_config": {"target_fps": 5.0}},
        ).json()
        return {"project": project, "track": result["tracks"][0]}
    finally:
        app.dependency_overrides.pop(get_default_detector, None)


def test_low_confidence_queue_only_includes_unreviewed_sorted_ascending(tmp_path):
    project = client.post("/projects", json={"name": "Low Confidence Queue Project"}).json()

    app.dependency_overrides[get_default_detector] = lambda: _ConfidenceDetector(0.9)
    try:
        video_a = create_synthetic_video(tmp_path / "a.mp4", frame_count=30, fps=10.0)
        source_a = client.post(f"/projects/{project['id']}/sources", json={"path": str(video_a)}).json()
        client.post(
            f"/projects/{project['id']}/sources/{source_a['id']}/process", json={"sampling_config": {"target_fps": 5.0}}
        )
    finally:
        app.dependency_overrides.pop(get_default_detector, None)

    # ByteTrack requires roughly >=0.7 confidence to activate a track at
    # all, so "low confidence" here still has to clear that floor.
    app.dependency_overrides[get_default_detector] = lambda: _ConfidenceDetector(0.75)
    try:
        video_b = create_synthetic_video(tmp_path / "b.mp4", frame_count=30, fps=10.0)
        source_b = client.post(f"/projects/{project['id']}/sources", json={"path": str(video_b)}).json()
        result_b = client.post(
            f"/projects/{project['id']}/sources/{source_b['id']}/process", json={"sampling_config": {"target_fps": 5.0}}
        ).json()
    finally:
        app.dependency_overrides.pop(get_default_detector, None)

    # Review a third, distinctly-confident track away - it should drop out of the queue.
    app.dependency_overrides[get_default_detector] = lambda: _ConfidenceDetector(0.8)
    try:
        video_c = create_synthetic_video(tmp_path / "c.mp4", frame_count=30, fps=10.0)
        source_c = client.post(f"/projects/{project['id']}/sources", json={"path": str(video_c)}).json()
        result_c = client.post(
            f"/projects/{project['id']}/sources/{source_c['id']}/process", json={"sampling_config": {"target_fps": 5.0}}
        ).json()
    finally:
        app.dependency_overrides.pop(get_default_detector, None)
    track_c = result_c["tracks"][0]
    frame_id = client.get(f"/tracks/{track_c['id']}").json()["frames"][0]["id"]
    client.put(f"/tracks/{track_c['id']}/review", json={"frame_candidate_id": frame_id, "decision": "accepted", "class_id": 4})

    queue = client.get(f"/projects/{project['id']}/active-learning/low-confidence-queue").json()

    track_ids = [item["track_id"] for item in queue]
    assert track_c["id"] not in track_ids  # reviewed, excluded
    assert track_ids[0] == result_b["tracks"][0]["id"]  # 0.3 confidence comes before 0.9
    confidences = [item["confidence"] for item in queue]
    assert confidences == sorted(confidences)


def test_hard_failed_queue_includes_failed_and_hard_tracks(tmp_path):
    ctx = _create_track(tmp_path, "Hard Failed Queue Project")
    project, track = ctx["project"], ctx["track"]
    frame_id = client.get(f"/tracks/{track['id']}").json()["frames"][0]["id"]

    client.put(f"/tracks/{track['id']}/review", json={"frame_candidate_id": frame_id, "decision": "failed"})

    queue = client.get(f"/projects/{project['id']}/active-learning/hard-failed-queue").json()
    assert len(queue) == 1
    assert queue[0]["track_id"] == track["id"]
    assert queue[0]["review_status"] == "failed"


def test_hard_failed_queue_keeps_a_pipeline_hard_track_even_once_accepted(tmp_path):
    # By design this queue surfaces every track the *pipeline* flagged
    # difficult (bucket) as well as every human-flagged one
    # (review_status) - accepting a HARD-bucket track doesn't erase
    # that it was flagged, so it stays visible for curation/QA.
    ctx = _create_track(tmp_path, "Accepted Still Flagged Project")
    project, track = ctx["project"], ctx["track"]
    assert track["bucket"] == "HARD"  # low-quality synthetic frames always rank HARD
    frame_id = client.get(f"/tracks/{track['id']}").json()["frames"][0]["id"]

    client.put(f"/tracks/{track['id']}/review", json={"frame_candidate_id": frame_id, "decision": "accepted", "class_id": 4})

    queue = client.get(f"/projects/{project['id']}/active-learning/hard-failed-queue").json()
    assert len(queue) == 1
    assert queue[0]["track_id"] == track["id"]
    assert queue[0]["review_status"] == "accepted"
    assert queue[0]["bucket"] == "HARD"


def test_disagreement_flagged_when_human_class_inconsistent_with_detector(tmp_path):
    ctx = _create_track(tmp_path, "Disagreement Project")
    project, track = ctx["project"], ctx["track"]
    frame_id = client.get(f"/tracks/{track['id']}").json()["frames"][0]["id"]

    # StubDetector-equivalent always reports "car" -> only class_id 4 (Car/Jeep/Van) is plausible.
    client.put(
        f"/tracks/{track['id']}/review",
        json={"frame_candidate_id": frame_id, "decision": "accepted", "class_id": 11},  # Truck 3-Axle
    )

    disagreements = client.get(f"/projects/{project['id']}/active-learning/disagreements").json()
    assert len(disagreements) == 1
    assert disagreements[0]["track_id"] == track["id"]
    assert disagreements[0]["detector_class"] == "car"
    assert disagreements[0]["human_class_id"] == 11
    assert disagreements[0]["human_class_name"] == "Truck 3-Axle"


def test_no_disagreement_when_human_class_is_consistent(tmp_path):
    ctx = _create_track(tmp_path, "No Disagreement Project")
    project, track = ctx["project"], ctx["track"]
    frame_id = client.get(f"/tracks/{track['id']}").json()["frames"][0]["id"]

    client.put(
        f"/tracks/{track['id']}/review",
        json={"frame_candidate_id": frame_id, "decision": "accepted", "class_id": 4},  # Car/Jeep/Van
    )

    disagreements = client.get(f"/projects/{project['id']}/active-learning/disagreements").json()
    assert disagreements == []


def test_retraining_handoff_generates_data_yaml_and_instructions(tmp_path):
    ctx = _create_track(tmp_path, "Retraining Handoff Project")
    project, track = ctx["project"], ctx["track"]
    frame_id = client.get(f"/tracks/{track['id']}").json()["frames"][0]["id"]
    client.put(f"/tracks/{track['id']}/review", json={"frame_candidate_id": frame_id, "decision": "accepted", "class_id": 4})

    exported = client.post(f"/projects/{project['id']}/dataset-versions", json={}).json()
    version_id = exported["dataset_version"]["id"]

    response = client.post(f"/dataset-versions/{version_id}/retraining-handoff")
    assert response.status_code == 200
    body = response.json()

    assert "data.yaml" in body["data_yaml_path"]
    assert "nc: 20" in body["data_yaml_content"]
    assert "Car/Jeep/Van" in body["data_yaml_content"]
    assert "images/train" in body["data_yaml_content"]
    assert "yolo detect train data=data.yaml" in body["instructions_content"]


def test_retraining_handoff_for_unknown_version_returns_404():
    response = client.post("/dataset-versions/does-not-exist/retraining-handoff")
    assert response.status_code == 404
