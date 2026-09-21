"""Taking a dataset version off this machine.

The export already writes a YOLO folder. What was missing was a way
to get it somewhere with a real GPU: "I want to export the data, make
it into a zip, download it, and train it on some other machine".
"""

import io
import json
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app
from app.ml.types import Detection
from tests.job_execution import process_source_sync
from tests.video_factory import create_synthetic_video

client = TestClient(app)

WIDTH, HEIGHT = 128, 96
PLATE_CLASS = 4


class _OnePlate:
    model_version = "one-plate-stub-v1"
    class_names = {0: "plate"}

    def detect(self, frame):
        return [Detection(bbox_xyxy=(20.0, 20.0, 80.0, 70.0), class_id=0, confidence=0.9)]


def _exported(tmp_path, name: str) -> tuple[dict, dict]:
    project = client.post("/projects", json={"name": name}).json()
    video = create_synthetic_video(tmp_path / "clip.mp4", frame_count=6, fps=10.0, width=WIDTH, height=HEIGHT)
    source = client.post(f"/projects/{project['id']}/sources", json={"path": str(video)}).json()
    process_source_sync(client, project["id"], source["id"], _OnePlate(), target_fps=10.0)
    for frame in client.get(f"/projects/{project['id']}/frames").json():
        client.put(
            f"/frames/{frame['id']}/annotations",
            json={"annotations": [{"class_id": PLATE_CLASS, "bbox_json": [20.0, 20.0, 80.0, 70.0]}]},
        )
    exported = client.post(f"/projects/{project['id']}/dataset-versions", json={})
    assert exported.status_code in (200, 201), exported.text
    return project, exported.json()["dataset_version"]


def _archive(version: dict) -> zipfile.ZipFile:
    response = client.get(f"/dataset-versions/{version['id']}/archive")
    assert response.status_code == 200, response.text
    return zipfile.ZipFile(io.BytesIO(response.content))


# --- what comes down ----------------------------------------------------------


def test_the_archive_is_a_readable_zip(tmp_path):
    _, version = _exported(tmp_path, "Readable Zip")

    assert _archive(version).testzip() is None


def test_it_carries_the_images_and_their_labels(tmp_path):
    _, version = _exported(tmp_path, "Images And Labels")

    names = _archive(version).namelist()

    assert any(n.startswith("images/") and n.endswith(".jpg") for n in names)
    assert any(n.startswith("labels/") and n.endswith(".txt") for n in names)


def test_it_carries_what_the_dataset_means(tmp_path):
    """Without these it is a folder of pictures."""
    _, version = _exported(tmp_path, "Meaning Too")

    names = _archive(version).namelist()

    assert {"data.yaml", "classes.txt", "manifest.json", "RETRAINING.md"} <= set(names)


def test_every_image_has_a_label_file(tmp_path):
    _, version = _exported(tmp_path, "Paired Up")
    names = _archive(version).namelist()

    images = {n[len("images/"):-len(".jpg")] for n in names if n.startswith("images/") and n.endswith(".jpg")}
    labels = {n[len("labels/"):-len(".txt")] for n in names if n.startswith("labels/") and n.endswith(".txt")}

    assert images == labels


def test_it_holds_as_many_images_as_the_manifest_counted(tmp_path):
    _, version = _exported(tmp_path, "Counts Agree")
    archive = _archive(version)
    manifest = json.loads(archive.read("manifest.json"))

    images = [n for n in archive.namelist() if n.startswith("images/") and n.endswith(".jpg")]

    assert len(images) == manifest["counts"]["total"]


# --- usable on the other machine ----------------------------------------------


def test_the_data_yaml_has_no_path_from_this_machine(tmp_path):
    """The whole point of the download. An absolute path is exactly
    what breaks the moment the dataset is somewhere else."""
    project, version = _exported(tmp_path, "No Local Paths")

    data_yaml = _archive(version).read("data.yaml").decode("utf-8")

    assert "path:" not in data_yaml
    assert project["workspace_path"] not in data_yaml


def test_the_data_yaml_still_names_the_splits_and_classes(tmp_path):
    _, version = _exported(tmp_path, "Still Complete")

    data_yaml = _archive(version).read("data.yaml").decode("utf-8")

    assert "train: images/train" in data_yaml
    assert "nc:" in data_yaml and "names:" in data_yaml


def test_the_instructions_are_for_somebody_holding_only_the_zip(tmp_path):
    _, version = _exported(tmp_path, "Instructions")

    readme = _archive(version).read("RETRAINING.md").decode("utf-8")

    assert "pip install ultralytics" in readme
    assert "yolo detect train data=data.yaml" in readme


def test_the_local_data_yaml_is_left_alone(tmp_path):
    """Training in this app depends on that absolute path, so the
    archive's portable copy must not overwrite it."""
    project, version = _exported(tmp_path, "Local Untouched")
    client.post(f"/dataset-versions/{version['id']}/retraining-handoff")
    local = Path(project["workspace_path"]) / "exports" / f"v{version['version']}" / "data.yaml"

    _archive(version)

    assert "path:" in local.read_text(encoding="utf-8")


# --- the download itself ------------------------------------------------------


def test_it_is_named_after_the_project_and_version(tmp_path):
    """A downloads folder full of dataset.zip is no use to anyone."""
    _, version = _exported(tmp_path, "Named Well")

    response = client.get(f"/dataset-versions/{version['id']}/archive")

    assert f"Named-Well-v{version['version']}-yolo.zip" in response.headers["content-disposition"]


def test_it_is_sent_as_a_download(tmp_path):
    _, version = _exported(tmp_path, "As Download")

    response = client.get(f"/dataset-versions/{version['id']}/archive")

    assert response.headers["content-type"] == "application/zip"
    assert response.headers["content-disposition"].startswith("attachment")


def test_the_listing_says_what_it_weighs(tmp_path):
    """So the click is an informed one."""
    project, _ = _exported(tmp_path, "Weighs Something")

    versions = client.get(f"/projects/{project['id']}/dataset-versions").json()

    assert versions[0]["bytes_on_disk"] > 0


def test_a_version_whose_files_are_gone_says_so(tmp_path):
    import shutil

    project, version = _exported(tmp_path, "Files Gone")
    shutil.rmtree(Path(project["workspace_path"]) / "exports" / f"v{version['version']}")

    response = client.get(f"/dataset-versions/{version['id']}/archive")

    assert response.status_code == 404


def test_an_unknown_version_is_not_found(tmp_path):
    assert client.get("/dataset-versions/nope/archive").status_code == 404
