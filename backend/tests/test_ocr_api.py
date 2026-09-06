from fastapi.testclient import TestClient

from app.main import app
from app.ml.factory import get_default_detector, get_default_ocr_engine
from app.ml.types import PlateOcrCandidate
from tests.stub_detector import StubDetector
from tests.stub_ocr_engine import StubOcrEngine
from tests.video_factory import create_synthetic_video

client = TestClient(app)


def _create_track(tmp_path, name: str = "OCR API Project") -> dict:
    app.dependency_overrides[get_default_detector] = lambda: StubDetector()
    try:
        project = client.post("/projects", json={"name": name}).json()
        video = create_synthetic_video(tmp_path / "clip.mp4", frame_count=60, fps=10.0)
        source = client.post(f"/projects/{project['id']}/sources", json={"path": str(video)}).json()
        result = client.post(
            f"/projects/{project['id']}/sources/{source['id']}/process",
            json={"sampling_config": {"target_fps": 5.0}},
        ).json()
        return {"project": project, "track": result["tracks"][0]}
    finally:
        app.dependency_overrides.pop(get_default_detector, None)


def test_run_ocr_persists_and_lists_candidates(tmp_path):
    ctx = _create_track(tmp_path)
    track_id = ctx["track"]["id"]

    app.dependency_overrides[get_default_ocr_engine] = lambda: StubOcrEngine(
        responses=[[PlateOcrCandidate(bbox_xyxy=(0, 0, 10, 5), text="KA05MN1234", confidence=0.92)]]
    )
    try:
        response = client.post(f"/tracks/{track_id}/ocr")
        assert response.status_code == 200
        attempts = response.json()
        assert len(attempts) >= 1
        assert any(a["normalized_text"] == "KA05MN1234" and a["selected"] for a in attempts)

        listed = client.get(f"/tracks/{track_id}/ocr-candidates").json()
        assert len(listed) == len(attempts)
    finally:
        app.dependency_overrides.pop(get_default_ocr_engine, None)


def test_run_ocr_with_no_text_returns_empty_list(tmp_path):
    ctx = _create_track(tmp_path)
    track_id = ctx["track"]["id"]

    app.dependency_overrides[get_default_ocr_engine] = lambda: StubOcrEngine(responses=[[]])
    try:
        response = client.post(f"/tracks/{track_id}/ocr")
        assert response.status_code == 200
        assert response.json() == []
    finally:
        app.dependency_overrides.pop(get_default_ocr_engine, None)


def test_ocr_run_on_unknown_track_returns_404():
    response = client.post("/tracks/does-not-exist/ocr")
    assert response.status_code == 404


def test_select_ocr_candidate_by_id(tmp_path):
    ctx = _create_track(tmp_path)
    track_id = ctx["track"]["id"]

    app.dependency_overrides[get_default_ocr_engine] = lambda: StubOcrEngine(
        responses=[[PlateOcrCandidate(bbox_xyxy=(0, 0, 10, 5), text="AAA1111", confidence=0.3)]]
    )
    try:
        client.post(f"/tracks/{track_id}/ocr")
        candidates = client.get(f"/tracks/{track_id}/ocr-candidates").json()
        assert len(candidates) >= 2
        not_selected = next(c for c in candidates if not c["selected"])

        response = client.put(f"/tracks/{track_id}/ocr-selection", json={"ocr_candidate_id": not_selected["id"]})
        assert response.status_code == 200
        assert response.json()["id"] == not_selected["id"]
        assert response.json()["selected"] is True
    finally:
        app.dependency_overrides.pop(get_default_ocr_engine, None)


def test_human_correction_creates_selected_human_row(tmp_path):
    ctx = _create_track(tmp_path)
    track_id = ctx["track"]["id"]
    timeline = client.get(f"/tracks/{track_id}").json()
    frame_id = timeline["frames"][0]["id"]

    response = client.put(
        f"/tracks/{track_id}/ocr-selection",
        json={"corrected_text": "dl 3c ab 0007", "frame_candidate_id": frame_id},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "human"
    assert body["normalized_text"] == "DL3CAB0007"
    assert body["selected"] is True


def test_selection_requires_exactly_one_of_the_two_options(tmp_path):
    ctx = _create_track(tmp_path)
    track_id = ctx["track"]["id"]

    both = client.put(
        f"/tracks/{track_id}/ocr-selection",
        json={"ocr_candidate_id": "x", "corrected_text": "y", "frame_candidate_id": "z"},
    )
    assert both.status_code == 422

    neither = client.put(f"/tracks/{track_id}/ocr-selection", json={})
    assert neither.status_code == 422

    missing_frame = client.put(f"/tracks/{track_id}/ocr-selection", json={"corrected_text": "y"})
    assert missing_frame.status_code == 422
