import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app
from app.ml.factory import get_default_detector
from tests.stub_detector import StubDetector
from tests.video_factory import create_synthetic_video

client = TestClient(app)


def _create_and_accept_track(tmp_path, name: str, class_id: int = 4) -> dict:
    """A project with exactly one track, reviewed and accepted."""
    app.dependency_overrides[get_default_detector] = lambda: StubDetector()
    try:
        project = client.post("/projects", json={"name": name}).json()
        video = create_synthetic_video(tmp_path / f"{name}.mp4", frame_count=30, fps=10.0)
        source = client.post(f"/projects/{project['id']}/sources", json={"path": str(video)}).json()
        result = client.post(
            f"/projects/{project['id']}/sources/{source['id']}/process",
            json={"sampling_config": {"target_fps": 5.0}},
        ).json()
        track = result["tracks"][0]
        timeline = client.get(f"/tracks/{track['id']}").json()
        frame_id = timeline["frames"][0]["id"]

        review = client.put(
            f"/tracks/{track['id']}/review",
            json={"frame_candidate_id": frame_id, "decision": "accepted", "class_id": class_id},
        ).json()
        return {"project": project, "track": review["track"], "annotation": review["annotation"]}
    finally:
        app.dependency_overrides.pop(get_default_detector, None)


def test_export_with_no_accepted_annotations_fails(tmp_path):
    project = client.post("/projects", json={"name": "Empty Export Project"}).json()
    response = client.post(f"/projects/{project['id']}/dataset-versions", json={})
    assert response.status_code == 400
    assert response.json()["code"] == "nothing_to_export"


def test_export_creates_a_validated_yolo_dataset(tmp_path):
    ctx = _create_and_accept_track(tmp_path, "Dataset Export Project")
    project = ctx["project"]

    response = client.post(f"/projects/{project['id']}/dataset-versions", json={"split_seed": 42})
    assert response.status_code == 201
    body = response.json()

    assert body["dataset_version"]["version"] == 1
    assert body["dataset_version"]["split_seed"] == 42
    assert body["validation"]["valid"] is True
    assert body["validation"]["errors"] == []
    assert body["counts"]["total"] == 1

    workspace = Path(project["workspace_path"])
    export_dir = workspace / "exports" / "v1"
    assert (export_dir / "classes.txt").is_file()
    class_lines = (export_dir / "classes.txt").read_text(encoding="utf-8").splitlines()
    assert len(class_lines) == 20
    assert class_lines[3] == "Car/Jeep/Van"  # class_id 4 -> 0-indexed line 3

    manifest = json.loads((export_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["counts"]["total"] == 1
    item = manifest["items"][0]
    assert item["class_id"] == 4
    assert item["class_name"] == "Car/Jeep/Van"
    assert item["track_id"] == ctx["track"]["id"]

    image_path = export_dir / item["image_path"]
    label_path = export_dir / item["label_path"]
    assert image_path.is_file()
    assert label_path.is_file()

    label_line = label_path.read_text(encoding="utf-8").strip()
    parts = label_line.split()
    assert len(parts) == 5
    assert parts[0] == "3"  # class_id 4 -> 0-indexed class 3
    coords = [float(p) for p in parts[1:]]
    assert all(0.0 <= c <= 1.0 for c in coords)


def test_two_exports_from_the_same_project_get_incrementing_versions(tmp_path):
    ctx = _create_and_accept_track(tmp_path, "Incrementing Version Project")
    project = ctx["project"]

    first = client.post(f"/projects/{project['id']}/dataset-versions", json={}).json()
    second = client.post(f"/projects/{project['id']}/dataset-versions", json={}).json()

    assert first["dataset_version"]["version"] == 1
    assert second["dataset_version"]["version"] == 2


def test_export_excludes_hard_and_failed_tracks(tmp_path):
    app.dependency_overrides[get_default_detector] = lambda: StubDetector()
    try:
        project = client.post("/projects", json={"name": "Mixed Status Project"}).json()
        video = create_synthetic_video(tmp_path / "mixed.mp4", frame_count=30, fps=10.0)
        source = client.post(f"/projects/{project['id']}/sources", json={"path": str(video)}).json()
        result = client.post(
            f"/projects/{project['id']}/sources/{source['id']}/process",
            json={"sampling_config": {"target_fps": 5.0}},
        ).json()
        track = result["tracks"][0]
        timeline = client.get(f"/tracks/{track['id']}").json()
        frame_id = timeline["frames"][0]["id"]

        client.put(
            f"/tracks/{track['id']}/review",
            json={"frame_candidate_id": frame_id, "decision": "failed"},
        )
    finally:
        app.dependency_overrides.pop(get_default_detector, None)

    response = client.post(f"/projects/{project['id']}/dataset-versions", json={})
    assert response.status_code == 400
    assert response.json()["code"] == "nothing_to_export"


def test_list_and_get_dataset_version(tmp_path):
    ctx = _create_and_accept_track(tmp_path, "List Dataset Versions Project")
    project = ctx["project"]
    created = client.post(f"/projects/{project['id']}/dataset-versions", json={}).json()

    listed = client.get(f"/projects/{project['id']}/dataset-versions").json()
    assert len(listed) == 1
    assert listed[0]["id"] == created["dataset_version"]["id"]

    fetched = client.get(f"/dataset-versions/{created['dataset_version']['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["id"] == created["dataset_version"]["id"]


def test_get_manifest_and_revalidate(tmp_path):
    ctx = _create_and_accept_track(tmp_path, "Manifest Project")
    project = ctx["project"]
    created = client.post(f"/projects/{project['id']}/dataset-versions", json={}).json()
    version_id = created["dataset_version"]["id"]

    manifest = client.get(f"/dataset-versions/{version_id}/manifest")
    assert manifest.status_code == 200
    assert manifest.json()["counts"]["total"] == 1

    validation = client.get(f"/dataset-versions/{version_id}/validate")
    assert validation.status_code == 200
    assert validation.json()["valid"] is True


def test_revalidate_detects_a_deleted_image_file(tmp_path):
    ctx = _create_and_accept_track(tmp_path, "Rotted Export Project")
    project = ctx["project"]
    created = client.post(f"/projects/{project['id']}/dataset-versions", json={}).json()
    version_id = created["dataset_version"]["id"]

    manifest = client.get(f"/dataset-versions/{version_id}/manifest").json()
    workspace = Path(project["workspace_path"])
    image_path = workspace / "exports" / "v1" / manifest["items"][0]["image_path"]
    image_path.unlink()

    validation = client.get(f"/dataset-versions/{version_id}/validate").json()
    assert validation["valid"] is False
    assert any("missing image file" in e for e in validation["errors"])


def test_invalid_ratios_are_rejected(tmp_path):
    ctx = _create_and_accept_track(tmp_path, "Bad Ratios Project")
    project = ctx["project"]
    response = client.post(
        f"/projects/{project['id']}/dataset-versions",
        json={"train_ratio": 0.5, "val_ratio": 0.3, "test_ratio": 0.3},
    )
    assert response.status_code == 422
