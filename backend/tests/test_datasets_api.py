import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app
from tests.job_execution import process_source_sync, tracks_for_run
from tests.stub_detector import StubDetector
from tests.video_factory import create_synthetic_video

client = TestClient(app)


def _create_and_accept_track(tmp_path, name: str, class_id: int = 4) -> dict:
    """A project with exactly one track, reviewed and accepted."""
    project = client.post("/projects", json={"name": name}).json()
    video = create_synthetic_video(tmp_path / f"{name}.mp4", frame_count=30, fps=10.0)
    source = client.post(f"/projects/{project['id']}/sources", json={"path": str(video)}).json()
    submitted = process_source_sync(client, project["id"], source["id"], StubDetector())
    track = tracks_for_run(client, project["id"], submitted["run_id"])[0]
    timeline = client.get(f"/tracks/{track['id']}").json()
    frame_id = timeline["frames"][0]["id"]

    review = client.put(
        f"/tracks/{track['id']}/review",
        json={"frame_candidate_id": frame_id, "decision": "accepted", "class_id": class_id},
    ).json()
    return {"project": project, "track": review["track"], "annotation": review["annotation"]}


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
    assert manifest["object_counts"]["total"] == 1
    item = manifest["items"][0]
    obj = item["objects"][0]
    assert obj["class_id"] == 4
    assert obj["class_name"] == "Car/Jeep/Van"
    assert obj["track_id"] == ctx["track"]["id"]

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


def test_export_writes_full_frames_not_crops(tmp_path):
    """Phase 10 DoD: exported images are full source frames and the boxes
    on them cover a realistic fraction of the image, rather than the old
    crop-per-track export whose single box filled the whole picture."""
    import cv2

    ctx = _create_and_accept_track(tmp_path, "Full Frame Export Project")
    project = ctx["project"]

    created = client.post(f"/projects/{project['id']}/dataset-versions", json={}).json()
    assert created["validation"]["valid"] is True

    workspace = Path(project["workspace_path"])
    export_dir = workspace / "exports" / "v1"
    manifest = json.loads((export_dir / "manifest.json").read_text(encoding="utf-8"))
    item = manifest["items"][0]

    source = client.get(f"/projects/{project['id']}/sources").json()[0]
    image = cv2.imread(str(export_dir / item["image_path"]))
    height, width = image.shape[:2]
    assert (width, height) == (source["width"], source["height"])

    _, _, box_width, box_height = [float(p) for p in item["objects"][0]["bbox_yolo"]]
    assert box_width * box_height < 0.98, "box fills the image - this is a crop, not a full frame"

    assert not any("looks like a cropped image" in w for w in created["validation"]["warnings"])


class _TwoVehicleDetector:
    """Two well-separated vehicles in every frame, so the tracker yields
    two distinct tracks that share the same frames."""

    def __init__(self) -> None:
        self.model_version = "stub-two-vehicle-v1"
        self.class_names = {0: "car"}

    def detect(self, frame):
        from app.ml.types import Detection

        return [
            Detection(bbox_xyxy=(2.0, 2.0, 20.0, 20.0), class_id=0, confidence=0.9),
            Detection(bbox_xyxy=(40.0, 26.0, 60.0, 44.0), class_id=0, confidence=0.9),
        ]


def test_two_vehicles_in_one_frame_export_as_one_image_with_two_boxes(tmp_path):
    """The core Phase 10 behaviour: a frame is the unit of export. Two
    vehicles in one frame must share a single image with both boxes in
    one label file - not two images, and never split across train/val,
    which would put the same pixels in two splits each missing a box."""
    project = client.post("/projects", json={"name": "Two Vehicle Project"}).json()
    video = create_synthetic_video(tmp_path / "two.mp4", frame_count=20, fps=10.0)
    source = client.post(f"/projects/{project['id']}/sources", json={"path": str(video)}).json()
    submitted = process_source_sync(client, project["id"], source["id"], _TwoVehicleDetector())
    result = {"tracks": tracks_for_run(client, project["id"], submitted["run_id"])}
    assert len(result["tracks"]) == 2

    # Accept both tracks on the same frame index, so both annotations
    # land on one shared frame.
    for track, class_id in zip(result["tracks"], (4, 7)):
        timeline = client.get(f"/tracks/{track['id']}").json()
        client.put(
            f"/tracks/{track['id']}/review",
            json={
                "frame_candidate_id": timeline["frames"][0]["id"],
                "decision": "accepted",
                "class_id": class_id,
            },
        )

    created = client.post(f"/projects/{project['id']}/dataset-versions", json={}).json()
    assert created["validation"]["valid"] is True

    export_dir = Path(project["workspace_path"]) / "exports" / "v1"
    manifest = json.loads((export_dir / "manifest.json").read_text(encoding="utf-8"))

    assert manifest["counts"]["total"] == 1, "two vehicles in one frame should export one image"
    assert manifest["object_counts"]["total"] == 2

    item = manifest["items"][0]
    assert len(item["objects"]) == 2
    assert {o["class_id"] for o in item["objects"]} == {4, 7}

    label_lines = (export_dir / item["label_path"]).read_text(encoding="utf-8").strip().splitlines()
    assert len(label_lines) == 2
    assert {line.split()[0] for line in label_lines} == {"3", "6"}  # class ids 4/7 -> 0-indexed


def test_export_warns_when_a_frame_has_unlabeled_vehicles(tmp_path):
    """Exporting a frame where one vehicle is accepted and another was
    detected but never reviewed ships the second vehicle as background,
    which teaches the model to ignore it. That has to be surfaced, not
    silently shipped."""
    project = client.post("/projects", json={"name": "Partial Label Project"}).json()
    video = create_synthetic_video(tmp_path / "partial.mp4", frame_count=20, fps=10.0)
    source = client.post(f"/projects/{project['id']}/sources", json={"path": str(video)}).json()
    submitted = process_source_sync(client, project["id"], source["id"], _TwoVehicleDetector())
    result = {"tracks": tracks_for_run(client, project["id"], submitted["run_id"])}
    assert len(result["tracks"]) == 2

    # Accept only the first vehicle; the second stays unreviewed.
    track = result["tracks"][0]
    timeline = client.get(f"/tracks/{track['id']}").json()
    client.put(
        f"/tracks/{track['id']}/review",
        json={"frame_candidate_id": timeline["frames"][0]["id"], "decision": "accepted", "class_id": 4},
    )

    created = client.post(f"/projects/{project['id']}/dataset-versions", json={}).json()

    assert created["validation"]["valid"] is True, "a partially labeled frame is a warning, not an error"
    assert any("no accepted annotation" in w for w in created["validation"]["warnings"]), (
        f"expected a partial-label warning, got {created['validation']['warnings']}"
    )

    export_dir = Path(project["workspace_path"]) / "exports" / "v1"
    manifest = json.loads((export_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["partially_labeled_frames"] == 1
    assert manifest["items"][0]["unlabeled_detection_count"] == 1


def test_export_spanning_two_sources_keeps_frames_separate(tmp_path):
    """Export decodes one source at a time; two sources in one project
    must both be materialized and must not collide on filenames (frame
    indices restart at 0 for every video)."""
    project = client.post("/projects", json={"name": "Two Source Project"}).json()
    source_ids = []
    for name in ("clip_a", "clip_b"):
        video = create_synthetic_video(tmp_path / f"{name}.mp4", frame_count=20, fps=10.0)
        source = client.post(f"/projects/{project['id']}/sources", json={"path": str(video)}).json()
        source_ids.append(source["id"])
        submitted = process_source_sync(client, project["id"], source["id"], StubDetector())
        track = tracks_for_run(client, project["id"], submitted["run_id"])[0]
        timeline = client.get(f"/tracks/{track['id']}").json()
        client.put(
            f"/tracks/{track['id']}/review",
            json={
                "frame_candidate_id": timeline["frames"][0]["id"],
                "decision": "accepted",
                "class_id": 4,
            },
        )

    created = client.post(f"/projects/{project['id']}/dataset-versions", json={}).json()
    assert created["validation"]["valid"] is True
    assert created["validation"]["errors"] == []

    export_dir = Path(project["workspace_path"]) / "exports" / "v1"
    manifest = json.loads((export_dir / "manifest.json").read_text(encoding="utf-8"))

    assert manifest["counts"]["total"] == 2
    assert {item["source_id"] for item in manifest["items"]} == set(source_ids)
    # Both clips have a frame 0; the export filename must disambiguate them.
    assert len({item["image_path"] for item in manifest["items"]}) == 2
    for item in manifest["items"]:
        assert (export_dir / item["image_path"]).is_file()


def test_reprocessing_a_source_reuses_frame_rows(tmp_path):
    """A second processing run over the same source must reuse the
    existing frame rows rather than creating a parallel set - frames are
    a property of the source, not of a run."""
    from app.db.models.frame import Frame
    from app.db.session import SessionLocal

    project = client.post("/projects", json={"name": "Reprocess Project"}).json()
    video = create_synthetic_video(tmp_path / "reprocess.mp4", frame_count=20, fps=10.0)
    source = client.post(f"/projects/{project['id']}/sources", json={"path": str(video)}).json()
    body = {"sampling_config": {"target_fps": 5.0}}
    process_source_sync(client, project["id"], source["id"], StubDetector())

    db = SessionLocal()
    try:
        after_first = db.query(Frame).filter(Frame.source_id == source["id"]).count()
    finally:
        db.close()

    process_source_sync(client, project["id"], source["id"], StubDetector())

    db = SessionLocal()
    try:
        after_second = db.query(Frame).filter(Frame.source_id == source["id"]).count()
    finally:
        db.close()

    assert after_first > 0
    assert after_second == after_first, "reprocessing duplicated frame rows"


def test_two_exports_from_the_same_project_get_incrementing_versions(tmp_path):
    ctx = _create_and_accept_track(tmp_path, "Incrementing Version Project")
    project = ctx["project"]

    first = client.post(f"/projects/{project['id']}/dataset-versions", json={}).json()
    second = client.post(f"/projects/{project['id']}/dataset-versions", json={}).json()

    assert first["dataset_version"]["version"] == 1
    assert second["dataset_version"]["version"] == 2


def test_export_excludes_hard_and_failed_tracks(tmp_path):
    project = client.post("/projects", json={"name": "Mixed Status Project"}).json()
    video = create_synthetic_video(tmp_path / "mixed.mp4", frame_count=30, fps=10.0)
    source = client.post(f"/projects/{project['id']}/sources", json={"path": str(video)}).json()
    submitted = process_source_sync(client, project["id"], source["id"], StubDetector())
    result = {"tracks": tracks_for_run(client, project["id"], submitted["run_id"])}
    track = result["tracks"][0]
    timeline = client.get(f"/tracks/{track['id']}").json()
    frame_id = timeline["frames"][0]["id"]

    client.put(
        f"/tracks/{track['id']}/review",
        json={"frame_candidate_id": frame_id, "decision": "failed"},
    )

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
