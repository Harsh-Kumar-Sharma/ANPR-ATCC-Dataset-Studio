"""Importing a production node's YOLO export as a source to review."""

import json
import zipfile
from pathlib import Path

import cv2
import numpy as np
import yaml
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

WIDTH, HEIGHT = 64, 48

#: (cx, cy, w, h, confidence), normalised like a YOLO label.
STUD = (0.25, 0.5, 0.1, 0.1, 0.3)


def _project(preset: str = "anpr-v1") -> dict:
    return client.post("/projects", json={"name": f"Import {preset}", "class_preset": preset}).json()


def _jpeg(value: int) -> bytes:
    ok, encoded = cv2.imencode(".jpg", np.full((HEIGHT, WIDTH, 3), value, np.uint8))
    assert ok
    return encoded.tobytes()


def _write_dataset(zf_or_dir, images: list[tuple[str, list[tuple]]], names=None) -> None:
    """Lay a dataset out the way the production node exports one."""
    files: dict[str, bytes] = {
        "data.yaml": yaml.safe_dump({"path": ".", "train": "images/train", "names": names or {0: "1"}}).encode(),
    }
    meta = []
    for i, (name, boxes) in enumerate(images):
        files[f"images/train/{name}"] = _jpeg(20 + i)
        files[f"labels/train/{Path(name).stem}.txt"] = "".join(
            f"0 {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}\n" for cx, cy, w, h, _ in boxes
        ).encode()
        meta.append(
            json.dumps(
                {
                    "image": name,
                    "camera": 8,
                    "capturedAt": f"2026-11-11T05:{i:02d}:00Z",
                    "size": [WIDTH, HEIGHT],
                    "boxes": [[0, conf, cx, cy, w, h] for cx, cy, w, h, conf in boxes],
                }
            )
        )
    files["meta.jsonl"] = ("\n".join(meta) + "\n").encode()

    if isinstance(zf_or_dir, zipfile.ZipFile):
        for name, data in files.items():
            zf_or_dir.writestr(name, data)
    else:
        for name, data in files.items():
            target = zf_or_dir / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)


def _zip(path: Path, images: list[tuple[str, list[tuple]]], names=None, extra: dict[str, bytes] | None = None) -> bytes:
    with zipfile.ZipFile(path, "w") as zf:
        _write_dataset(zf, images, names)
        for name, data in (extra or {}).items():
            zf.writestr(name, data)
    return path.read_bytes()


def _upload(project: dict, body: bytes, filename: str = "dataset_anpr_camera8.zip"):
    return client.post(
        f"/projects/{project['id']}/sources/dataset/upload",
        params={"filename": filename},
        content=body,
        headers={"Content-Type": "application/zip"},
    )


def _week(prefix: str = "c8") -> list[tuple[str, list[tuple]]]:
    """Five pictures with a box on the same road stud, one plate the
    model was unsure of, and one it was sure of."""
    images = [(f"{prefix}_stud_{i}.jpg", [STUD]) for i in range(5)]
    images.append((f"{prefix}_unsure.jpg", [(0.7, 0.3, 0.2, 0.1, 0.35)]))
    images.append((f"{prefix}_sure.jpg", [(0.6, 0.6, 0.2, 0.1, 0.8)]))
    return images


def test_upload_becomes_a_source_of_frames_carrying_the_models_boxes(tmp_path):
    project = _project()
    response = _upload(project, _zip(tmp_path / "week.zip", _week()))

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["source"]["type"] == "dataset"
    assert body["source"]["path_or_uri"] == "dataset_anpr_camera8.zip"
    assert body["source"]["stored_frames"] == 7
    assert body["images_imported"] == 7
    assert body["boxes_imported"] == 7
    assert body["repeated_spot_frames"] == 5
    assert body["low_confidence_frames"] == 1
    # The production model calls its only class "1"; a one-class
    # dataset goes into the project's plate class.
    assert body["class_mapping"] == {"1": "number_plate"}

    frames = client.get(f"/projects/{project['id']}/frames", params={"source_id": body["source"]["id"]}).json()
    # The likeliest mistakes first: the stud, then the unsure plate.
    assert [f["selection_reason"] for f in frames] == ["repeated_spot"] * 5 + ["low_confidence", None]
    assert all(f["status"] == "pending" for f in frames)

    sure = frames[-1]
    assert (sure["width"], sure["height"]) == (WIDTH, HEIGHT)
    [box] = client.get(f"/frames/{sure['id']}/annotations").json()
    assert box["source"] == "model"
    assert box["confidence"] == 0.8
    assert box["bbox_json"] == [32.0, 26.4, 44.8, 31.2]

    image = client.get(f"/frames/{sure['id']}/image")
    assert image.status_code == 200


def test_saving_the_canvas_keeps_a_box_as_the_humans_and_drops_the_rest(tmp_path):
    project = _project()
    source = _upload(project, _zip(tmp_path / "week.zip", _week())).json()["source"]
    frames = client.get(f"/projects/{project['id']}/frames", params={"source_id": source["id"]}).json()
    stud, sure = frames[0], frames[-1]

    [kept] = client.get(f"/frames/{sure['id']}/annotations").json()
    saved = client.put(
        f"/frames/{sure['id']}/annotations",
        json={"annotations": [{"id": kept["id"], "class_id": kept["class_id"], "bbox_json": kept["bbox_json"]}]},
    )
    assert saved.status_code == 200, saved.text
    [box] = saved.json()
    assert (box["source"], box["status"]) == ("human", "accepted")
    # What the model thought stays on record after a person agreed.
    assert box["confidence"] == 0.8

    # The stud is not a plate: saving the frame without it removes it.
    assert client.put(f"/frames/{stud['id']}/annotations", json={"annotations": []}).status_code == 200
    assert client.get(f"/frames/{stud['id']}/annotations").json() == []
    assert client.get(f"/frames/{stud['id']}").json()["status"] == "labeled"


def test_the_same_export_twice_adds_nothing_and_a_new_week_adds_only_what_is_new(tmp_path):
    project = _project()
    assert _upload(project, _zip(tmp_path / "week1.zip", _week())).status_code == 201

    again = _upload(project, _zip(tmp_path / "week1-again.zip", _week()))
    assert again.status_code == 409
    assert again.json()["code"] == "dataset_already_imported"

    overlap = _week()[:1] + [("c8_next_week.jpg", [(0.5, 0.5, 0.2, 0.1, 0.9)])]
    second = _upload(project, _zip(tmp_path / "week2.zip", overlap))
    assert second.status_code == 201, second.text
    assert (second.json()["images_imported"], second.json()["already_imported"]) == (1, 1)


def test_a_class_with_nowhere_obvious_to_go_stops_the_import(tmp_path):
    # Twenty traffic classes and no plate class: guessing would put the
    # boxes in the wrong one.
    project = _project("atcc-v1")
    response = _upload(project, _zip(tmp_path / "week.zip", _week()))

    assert response.status_code == 409
    assert response.json()["code"] == "class_mapping_needed"
    assert client.get(f"/projects/{project['id']}/sources").json() == []


def test_zip_entries_that_point_outside_the_archive_are_ignored(tmp_path):
    project = _project()
    body = _zip(tmp_path / "week.zip", _week()[-1:], extra={"../escaped.jpg": _jpeg(1), "__MACOSX/._x.jpg": b"junk"})

    response = _upload(project, body)

    assert response.status_code == 201, response.text
    assert response.json()["images_imported"] == 1
    assert response.json()["unreadable"] == 0
    workspace = Path(project["workspace_path"])
    assert not (workspace / "escaped.jpg").exists()
    assert not (workspace.parent / "escaped.jpg").exists()


def test_a_folder_on_the_server_imports_like_its_zip(tmp_path):
    project = _project()
    folder = tmp_path / "dataset_anpr_camera3"
    _write_dataset(folder, _week("c3"))

    response = client.post(f"/projects/{project['id']}/sources/dataset", json={"path": str(folder)})

    assert response.status_code == 201, response.text
    assert response.json()["images_imported"] == 7
    assert response.json()["source"]["path_or_uri"] == "dataset_anpr_camera3"


def test_something_that_is_not_a_zip_is_refused(tmp_path):
    project = _project()
    response = _upload(project, b"not a zip at all", filename="notes.txt")

    assert response.status_code == 400
    assert response.json()["code"] == "dataset_not_a_zip"


def test_removing_the_source_removes_its_images(tmp_path):
    project = _project()
    source = _upload(project, _zip(tmp_path / "week.zip", _week())).json()["source"]
    images = Path(project["workspace_path"]) / "derived" / "frames" / source["id"]
    assert len(list(images.glob("*.jpg"))) == 7

    assert client.delete(f"/projects/{project['id']}/sources/{source['id']}").status_code == 200

    assert not images.exists()


def test_reviewed_frames_reach_the_export_and_unreviewed_and_rejected_ones_do_not(tmp_path):
    project = _project()
    source = _upload(project, _zip(tmp_path / "week.zip", _week())).json()["source"]
    frames = client.get(f"/projects/{project['id']}/frames", params={"source_id": source["id"]}).json()
    stud, unsure, sure = frames[0], frames[5], frames[6]

    for frame in (unsure, sure):
        [box] = client.get(f"/frames/{frame['id']}/annotations").json()
        client.put(
            f"/frames/{frame['id']}/annotations",
            json={"annotations": [{"id": box["id"], "class_id": box["class_id"], "bbox_json": box["bbox_json"]}]},
        )
    assert client.put(f"/frames/{stud['id']}/status", json={"status": "rejected"}).status_code == 200

    created = client.post(
        f"/projects/{project['id']}/dataset-versions", json={"train_ratio": 0.5, "val_ratio": 0.5, "test_ratio": 0.0}
    )

    assert created.status_code == 201, created.text
    assert created.json()["validation"]["valid"] is True, created.json()["validation"]["errors"]
    assert created.json()["counts"]["total"] == 2
    assert created.json()["object_counts"]["total"] == 2
