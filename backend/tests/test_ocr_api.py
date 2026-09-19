from fastapi.testclient import TestClient

from app.main import app
from app.ml.factory import get_default_ocr_engine
from app.ml.types import PlateOcrCandidate
from tests.job_execution import process_source_sync, tracks_for_run
from tests.stub_detector import StubDetector
from tests.stub_ocr_engine import StubOcrEngine
from tests.video_factory import create_synthetic_video

client = TestClient(app)


def _create_track(tmp_path, name: str = "OCR API Project") -> dict:
    project = client.post("/projects", json={"name": name}).json()
    video = create_synthetic_video(tmp_path / "clip.mp4", frame_count=60, fps=10.0)
    source = client.post(f"/projects/{project['id']}/sources", json={"path": str(video)}).json()
    submitted = process_source_sync(client, project["id"], source["id"], StubDetector())
    tracks = tracks_for_run(client, project["id"], submitted["run_id"])
    return {"project": project, "track": tracks[0]}


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


def test_the_ocr_table_holds_only_what_the_model_read(tmp_path):
    """Ticket 15. A human's reading is a property of the vehicle in the
    box, so it lives on the annotation; the old human-sourced row here
    was a second home that no export ever read. `selected` still means
    "the model's own best attempt", and nothing but the OCR run writes
    it."""
    ctx = _create_track(tmp_path)
    track_id = ctx["track"]["id"]

    app.dependency_overrides[get_default_ocr_engine] = lambda: StubOcrEngine(
        responses=[[PlateOcrCandidate(bbox_xyxy=(0, 0, 10, 5), text="AAA1111", confidence=0.3)]]
    )
    try:
        client.post(f"/tracks/{track_id}/ocr")
    finally:
        app.dependency_overrides.pop(get_default_ocr_engine, None)

    candidates = client.get(f"/tracks/{track_id}/ocr-candidates").json()
    assert candidates
    assert all(c["source"] == "model" for c in candidates)
    assert sum(1 for c in candidates if c["selected"]) == 1


def test_the_old_selection_endpoint_is_gone(tmp_path):
    """It wrote a human reading into the model's table. Replaced by
    PUT /tracks/{id}/plate-text, which writes the annotation.

    The request is made with a *valid* frame candidate, because the old
    endpoint answered 404 for an unknown one - so a 404 alone could not
    tell "route removed" from "bad id". The body distinguishes them:
    FastAPI's routing 404 has a `detail`, this app's has a `code`.
    """
    ctx = _create_track(tmp_path)
    track_id = ctx["track"]["id"]
    frame_id = client.get(f"/tracks/{track_id}").json()["frames"][0]["id"]

    response = client.put(
        f"/tracks/{track_id}/ocr-selection",
        json={"corrected_text": "dl 3c ab 0007", "frame_candidate_id": frame_id},
    )

    assert response.status_code == 404
    assert "code" not in response.json(), "this is routing saying no such path, not the app saying no such row"
