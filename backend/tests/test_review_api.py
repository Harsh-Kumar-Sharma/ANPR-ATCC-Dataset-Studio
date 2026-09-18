from fastapi.testclient import TestClient

from app.main import app
from tests.job_execution import process_source_sync, tracks_for_run
from tests.stub_detector import StubDetector
from tests.video_factory import create_synthetic_video

client = TestClient(app)


def _create_track(tmp_path, name: str = "Review API Project") -> dict:
    project = client.post("/projects", json={"name": name}).json()
    video = create_synthetic_video(tmp_path / "clip.mp4", frame_count=30, fps=10.0)
    source = client.post(f"/projects/{project['id']}/sources", json={"path": str(video)}).json()
    submitted = process_source_sync(client, project["id"], source["id"], StubDetector())
    tracks = tracks_for_run(client, project["id"], submitted["run_id"])
    return {"project": project, "track": tracks[0]}


def _first_frame_id(track_id: str) -> str:
    timeline = client.get(f"/tracks/{track_id}").json()
    return timeline["frames"][0]["id"]


def test_class_schema_has_20_atcc_classes():
    project = client.post("/projects", json={"name": "Class Schema Project"}).json()
    response = client.get(f"/projects/{project['id']}/class-schema")
    assert response.status_code == 200
    classes = response.json()
    assert len(classes) == 20
    assert {"id": 4, "name": "Car/Jeep/Van"} in classes


def test_accept_review_creates_human_annotation(tmp_path):
    ctx = _create_track(tmp_path)
    track_id = ctx["track"]["id"]
    frame_id = _first_frame_id(track_id)

    response = client.put(
        f"/tracks/{track_id}/review",
        json={"frame_candidate_id": frame_id, "decision": "accepted", "class_id": 4},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["track"]["review_status"] == "accepted"
    assert body["annotation"]["source"] == "human"
    assert body["annotation"]["class_id"] == 4
    assert body["annotation"]["status"] == "accepted"
    assert body["annotation"]["frame_candidate_id"] == frame_id

    fetched = client.get(f"/tracks/{track_id}/annotation")
    assert fetched.status_code == 200
    assert fetched.json()["id"] == body["annotation"]["id"]


def test_accepted_and_hard_decisions_require_class_id(tmp_path):
    ctx = _create_track(tmp_path)
    track_id = ctx["track"]["id"]
    frame_id = _first_frame_id(track_id)

    for decision in ("accepted", "hard"):
        response = client.put(
            f"/tracks/{track_id}/review",
            json={"frame_candidate_id": frame_id, "decision": decision},
        )
        assert response.status_code == 422


def test_failed_decision_does_not_require_class_id(tmp_path):
    ctx = _create_track(tmp_path)
    track_id = ctx["track"]["id"]
    frame_id = _first_frame_id(track_id)

    response = client.put(
        f"/tracks/{track_id}/review",
        json={"frame_candidate_id": frame_id, "decision": "failed"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["annotation"]["class_id"] is None
    assert body["track"]["review_status"] == "failed"


def test_invalid_class_id_is_rejected(tmp_path):
    ctx = _create_track(tmp_path)
    track_id = ctx["track"]["id"]
    frame_id = _first_frame_id(track_id)

    response = client.put(
        f"/tracks/{track_id}/review",
        json={"frame_candidate_id": frame_id, "decision": "accepted", "class_id": 999},
    )
    assert response.status_code == 400
    assert response.json()["code"] == "invalid_review"


def test_reviewing_twice_updates_the_same_annotation_not_a_duplicate(tmp_path):
    ctx = _create_track(tmp_path)
    track_id = ctx["track"]["id"]
    timeline = client.get(f"/tracks/{track_id}").json()
    frame_ids = [f["id"] for f in timeline["frames"]]

    first = client.put(
        f"/tracks/{track_id}/review",
        json={"frame_candidate_id": frame_ids[0], "decision": "hard", "class_id": 1},
    ).json()
    second = client.put(
        f"/tracks/{track_id}/review",
        json={"frame_candidate_id": frame_ids[1], "decision": "accepted", "class_id": 2},
    ).json()

    assert first["annotation"]["id"] == second["annotation"]["id"]
    assert second["annotation"]["frame_candidate_id"] == frame_ids[1]
    assert second["annotation"]["class_id"] == 2
    assert second["track"]["review_status"] == "accepted"


def test_review_with_frame_from_another_track_returns_404(tmp_path):
    ctx_a = _create_track(tmp_path, "Track A Project")
    ctx_b = _create_track(tmp_path, "Track B Project")
    frame_from_b = _first_frame_id(ctx_b["track"]["id"])

    response = client.put(
        f"/tracks/{ctx_a['track']['id']}/review",
        json={"frame_candidate_id": frame_from_b, "decision": "accepted", "class_id": 4},
    )
    assert response.status_code == 404


def test_frame_image_is_served(tmp_path):
    ctx = _create_track(tmp_path)
    frame_id = _first_frame_id(ctx["track"]["id"])

    response = client.get(f"/tracks/frames/{frame_id}/image")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert len(response.content) > 0


def test_frame_image_404_for_unknown_frame():
    response = client.get("/tracks/frames/does-not-exist/image")
    assert response.status_code == 404


def test_get_annotation_before_any_review_returns_404(tmp_path):
    ctx = _create_track(tmp_path)
    response = client.get(f"/tracks/{ctx['track']['id']}/annotation")
    assert response.status_code == 404


def test_bbox_defaults_to_frame_candidates_bbox_when_not_provided(tmp_path):
    ctx = _create_track(tmp_path)
    track_id = ctx["track"]["id"]
    timeline = client.get(f"/tracks/{track_id}").json()
    frame = timeline["frames"][0]

    response = client.put(
        f"/tracks/{track_id}/review",
        json={"frame_candidate_id": frame["id"], "decision": "accepted", "class_id": 4},
    )
    assert response.json()["annotation"]["bbox_json"] == frame["bbox_json"]


def test_bbox_can_be_overridden(tmp_path):
    ctx = _create_track(tmp_path)
    track_id = ctx["track"]["id"]
    frame_id = _first_frame_id(track_id)

    response = client.put(
        f"/tracks/{track_id}/review",
        json={
            "frame_candidate_id": frame_id,
            "decision": "accepted",
            "class_id": 4,
            "bbox_json": [1.0, 2.0, 30.0, 40.0],
        },
    )
    assert response.json()["annotation"]["bbox_json"] == [1.0, 2.0, 30.0, 40.0]
