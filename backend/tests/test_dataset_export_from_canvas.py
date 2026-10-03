"""Ticket 12: work done in the labelling canvas reaches a YOLO dataset.

Every test here labels frames the way a human does - through
``PUT /frames/{id}/annotations`` - and then exports. None of these boxes
has a frame candidate or a track, which is precisely why the old
track-keyed export could not see them.
"""

import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app
from app.ml.types import Detection
from tests.job_execution import process_source_sync
from tests.video_factory import create_synthetic_video

client = TestClient(app)

WIDTH, HEIGHT = 128, 96

#: Two boxes a human might draw, both comfortably inside the frame.
CAR = [5.0, 5.0, 45.0, 35.0]
TRUCK = [60.0, 50.0, 110.0, 85.0]


class _SteadyDetector:
    """One vehicle in the same place in every frame.

    A frame row is only created where the detector saw something, so
    detecting on every frame is how these tests get a full queue. The box
    does not drift because a drifting one walks out of a 128px frame.
    """

    def __init__(self, boxes: list[tuple[float, float, float, float]] | None = None) -> None:
        self.model_version = "stub-steady-v1"
        self.class_names = {0: "car"}
        self._boxes = boxes or [(10.0, 10.0, 50.0, 40.0)]

    def detect(self, frame):
        return [Detection(bbox_xyxy=box, class_id=0, confidence=0.9) for box in self._boxes]


def _project_with_queue(tmp_path, name: str, frame_count: int = 12, detector=None) -> tuple[dict, list[dict]]:
    """A project whose labelling queue holds one frame per sampled frame."""
    project = client.post("/projects", json={"name": name}).json()
    video = create_synthetic_video(tmp_path / f"{name}.mp4", frame_count=frame_count, fps=10.0, width=WIDTH, height=HEIGHT)
    source = client.post(f"/projects/{project['id']}/sources", json={"path": str(video)}).json()
    process_source_sync(client, project["id"], source["id"], detector or _SteadyDetector(), target_fps=10.0)

    queue = client.get(f"/projects/{project['id']}/frames").json()
    assert queue, "detection should have filled the labelling queue"
    return project, queue


def _draw(frame: dict, boxes: list[tuple[int, list[float]]]) -> list[dict]:
    """Save a complete set of boxes on a frame, as the canvas does."""
    response = client.put(
        f"/frames/{frame['id']}/annotations",
        json={"annotations": [{"class_id": class_id, "bbox_json": bbox} for class_id, bbox in boxes]},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _export(project: dict, **body) -> tuple[dict, dict]:
    created = client.post(f"/projects/{project['id']}/dataset-versions", json=body)
    assert created.status_code == 201, created.text
    created = created.json()
    export_dir = Path(project["workspace_path"]) / "exports" / f"v{created['dataset_version']['version']}"
    manifest = json.loads((export_dir / "manifest.json").read_text(encoding="utf-8"))
    return created, manifest


def test_boxes_drawn_on_the_canvas_reach_the_dataset(tmp_path):
    """The seam ticket 12 exists to close. Before it, a canvas box had no
    frame candidate, the export joined through one, and a human's whole
    afternoon of labelling exported as nothing at all."""
    project, queue = _project_with_queue(tmp_path, "Canvas Export")
    _draw(queue[0], [(4, CAR), (7, TRUCK)])

    created, manifest = _export(project)

    assert created["validation"]["valid"] is True, created["validation"]["errors"]
    assert manifest["counts"]["total"] == 1
    assert manifest["object_counts"]["total"] == 2

    item = manifest["items"][0]
    assert item["frame_id"] == queue[0]["id"]
    assert {o["class_id"] for o in item["objects"]} == {4, 7}

    export_dir = Path(project["workspace_path"]) / "exports" / "v1"
    label_lines = (export_dir / item["label_path"]).read_text(encoding="utf-8").strip().splitlines()
    assert len(label_lines) == 2, "both boxes belong in the one label file for that image"
    assert (export_dir / item["image_path"]).is_file()


def test_a_canvas_box_has_no_track_and_says_so(tmp_path):
    """Provenance stays in the manifest, but a canvas box genuinely has
    no track and no candidate. Recording null is honest; inventing one
    would make the manifest lie about where a label came from."""
    project, queue = _project_with_queue(tmp_path, "Canvas Provenance")
    _draw(queue[0], [(4, CAR)])

    _, manifest = _export(project)

    obj = manifest["items"][0]["objects"][0]
    assert obj["track_id"] is None
    assert obj["frame_candidate_id"] is None
    assert obj["annotation_id"]


def test_fifty_canvas_labeled_frames_export_as_a_valid_dataset(tmp_path):
    """Ticket 12's done-when, at the size it was written for."""
    project, queue = _project_with_queue(tmp_path, "Fifty Frames", frame_count=60)
    assert len(queue) >= 50

    labeled = queue[:50]
    for position, frame in enumerate(labeled):
        # Alternate one box and two, so the label files are not uniform
        # and a per-frame object count has something to get wrong.
        boxes = [(4, CAR)] if position % 2 else [(4, CAR), (7, TRUCK)]
        _draw(frame, boxes)

    created, manifest = _export(project, split_seed=42)

    assert created["validation"]["valid"] is True, created["validation"]["errors"]
    assert manifest["counts"]["total"] == 50
    assert manifest["object_counts"]["total"] == 75  # 25 frames x 2 boxes + 25 x 1

    export_dir = Path(project["workspace_path"]) / "exports" / "v1"
    for item in manifest["items"]:
        lines = [ln for ln in (export_dir / item["label_path"]).read_text(encoding="utf-8").splitlines() if ln.strip()]
        assert len(lines) == len(item["objects"])
        assert (export_dir / item["image_path"]).is_file()

    assert len({item["image_path"] for item in manifest["items"]}) == 50


def test_the_split_still_applies_to_canvas_labeled_frames(tmp_path):
    """Frame-level export must not quietly bypass the split: every frame
    still gets train/val/test from the same seeded assignment."""
    project, queue = _project_with_queue(tmp_path, "Canvas Split", frame_count=60)
    assert len(queue) >= 50
    for frame in queue[:50]:
        _draw(frame, [(4, CAR)])

    _, manifest = _export(project, split_seed=42)

    # 75/15/10 of fifty. One continuous clip is one passage, so this
    # falls back to splitting by frame - which is what is under test.
    assert manifest["counts"] == {"train": 38, "val": 8, "test": 4, "total": 50}
    assert {item["split"] for item in manifest["items"]} == {"train", "val", "test"}

    # Same frames, same ratios, same seed - same assignment.
    _, second = _export(project, split_seed=42)
    first_by_frame = {item["frame_id"]: item["split"] for item in manifest["items"]}
    second_by_frame = {item["frame_id"]: item["split"] for item in second["items"]}
    assert first_by_frame == second_by_frame


def test_a_rejected_frame_stays_out_even_though_it_is_labeled(tmp_path):
    """Rejecting is "not worth labelling", and it has to outrank the
    boxes already on the frame - otherwise the judgement is decorative."""
    project, queue = _project_with_queue(tmp_path, "Canvas Rejected")
    _draw(queue[0], [(4, CAR)])
    _draw(queue[1], [(4, CAR)])

    rejected = client.put(f"/frames/{queue[1]['id']}/status", json={"status": "rejected"})
    assert rejected.status_code == 200, rejected.text

    _, manifest = _export(project)

    assert manifest["counts"]["total"] == 1
    assert [item["frame_id"] for item in manifest["items"]] == [queue[0]["id"]]


def test_frames_nobody_has_labeled_are_not_exported(tmp_path):
    """A queue of sixty frames with one labelled exports one image, not
    fifty-nine blank ones."""
    project, queue = _project_with_queue(tmp_path, "Canvas Unlabeled", frame_count=20)
    _draw(queue[0], [(4, CAR)])

    _, manifest = _export(project)

    assert manifest["counts"]["total"] == 1
    assert manifest["items"][0]["frame_id"] == queue[0]["id"]


def test_a_frame_labeled_as_empty_exports_as_a_background_image(tmp_path):
    """Saving zero boxes is a real label - "I looked, there is nothing
    here" - and it is exactly the negative example a detector needs. It
    is not the same thing as a frame nobody has opened, and exporting it
    as background is what makes that distinction mean anything."""
    project, queue = _project_with_queue(tmp_path, "Canvas Background")
    _draw(queue[0], [(4, CAR)])
    _draw(queue[1], [])

    created, manifest = _export(project)

    assert created["validation"]["valid"] is True, created["validation"]["errors"]
    assert manifest["counts"]["total"] == 2
    assert manifest["object_counts"]["total"] == 1
    assert manifest["background_frames"] == 1

    background = next(item for item in manifest["items"] if item["frame_id"] == queue[1]["id"])
    assert background["objects"] == []

    export_dir = Path(project["workspace_path"]) / "exports" / "v1"
    assert (export_dir / background["image_path"]).is_file()
    label_path = export_dir / background["label_path"]
    assert label_path.is_file(), "a background image still needs its (empty) label file"
    assert label_path.read_text(encoding="utf-8").strip() == ""


def test_a_frame_a_human_finished_is_not_reported_as_partially_labeled(tmp_path):
    """The partial-label warning means "the detector found a vehicle
    nobody accepted". On a frame a human labelled box by box, every
    remaining detection is one they declined - so the warning would be
    telling them off for doing the job."""
    detector = _SteadyDetector(boxes=[(5.0, 5.0, 45.0, 35.0), (60.0, 50.0, 110.0, 85.0)])
    project, queue = _project_with_queue(tmp_path, "Canvas Complete", detector=detector)

    _draw(queue[0], [(4, CAR)])  # the human kept one and declined the other

    created, manifest = _export(project)

    assert manifest["items"][0]["unlabeled_detection_count"] == 0
    assert manifest["partially_labeled_frames"] == 0
    assert not any("no accepted annotation" in w for w in created["validation"]["warnings"])


def test_canvas_and_track_review_labels_export_side_by_side(tmp_path):
    """The two labelling paths coexist: nothing about frame-level export
    drops the boxes written the old way through track review."""
    from tests.job_execution import tracks_for_run

    project = client.post("/projects", json={"name": "Mixed Labelling"}).json()
    video = create_synthetic_video(tmp_path / "mixed.mp4", frame_count=12, fps=10.0, width=WIDTH, height=HEIGHT)
    source = client.post(f"/projects/{project['id']}/sources", json={"path": str(video)}).json()
    submitted = process_source_sync(client, project["id"], source["id"], _SteadyDetector(), target_fps=10.0)

    track = tracks_for_run(client, project["id"], submitted["run_id"])[0]
    timeline = client.get(f"/tracks/{track['id']}").json()
    reviewed = client.put(
        f"/tracks/{track['id']}/review",
        json={"frame_candidate_id": timeline["frames"][0]["id"], "decision": "accepted", "class_id": 4},
    )
    assert reviewed.status_code == 200, reviewed.text
    reviewed_frame_id = reviewed.json()["annotation"]["frame_id"]

    queue = client.get(f"/projects/{project['id']}/frames").json()
    canvas_frame = next(f for f in queue if f["id"] != reviewed_frame_id)
    _draw(canvas_frame, [(7, TRUCK)])

    created, manifest = _export(project)

    assert created["validation"]["valid"] is True, created["validation"]["errors"]
    assert manifest["counts"]["total"] == 2
    by_frame = {item["frame_id"]: item for item in manifest["items"]}
    assert by_frame[reviewed_frame_id]["objects"][0]["track_id"] == track["id"]
    assert by_frame[canvas_frame["id"]]["objects"][0]["track_id"] is None


def test_the_export_response_counts_images_not_boxes(tmp_path):
    """What the export reports back has to describe the dataset it just
    wrote. It used to count dataset-item rows, which are per *box* - fine
    while one frame meant one label, and wrong the moment a human draws
    two. It also missed background frames entirely, because those write
    an image and no items at all."""
    project, queue = _project_with_queue(tmp_path, "Export Counts")
    _draw(queue[0], [(4, CAR), (7, TRUCK)])
    _draw(queue[1], [(4, CAR), (7, TRUCK)])
    _draw(queue[2], [])

    created = client.post(f"/projects/{project['id']}/dataset-versions", json={})
    assert created.status_code == 201, created.text
    created = created.json()

    # Four boxes across three images. The numbers have to differ, or the
    # test cannot tell a per-box count from a per-image one.
    assert created["counts"]["total"] == 3, "three images were written"
    assert created["object_counts"]["total"] == 4, "four boxes were written across them"
    assert sum(created["counts"][s] for s in ("train", "val", "test")) == 3
