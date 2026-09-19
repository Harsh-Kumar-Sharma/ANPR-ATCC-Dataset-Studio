"""Ticket 13: what else is true about an object, beyond its class.

Plate text, colour, direction, occlusion, lighting. All of it lives in
the annotation's generic ``attributes`` JSON, so the next attribute
anyone wants is a line in one file and not a migration.
"""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.ml.types import Detection
from tests.job_execution import process_source_sync
from tests.video_factory import create_synthetic_video

client = TestClient(app)

WIDTH, HEIGHT = 128, 96
CAR = [5.0, 5.0, 45.0, 35.0]
TRUCK = [60.0, 50.0, 110.0, 85.0]


class _SteadyDetector:
    def __init__(self) -> None:
        self.model_version = "stub-steady-v1"
        self.class_names = {0: "car"}

    def detect(self, frame):
        return [Detection(bbox_xyxy=(10.0, 10.0, 50.0, 40.0), class_id=0, confidence=0.9)]


@pytest.fixture
def queue(tmp_path, request):
    """A project with a labelling queue, named after the test using it."""
    name = request.node.name[:40]
    project = client.post("/projects", json={"name": name}).json()
    video = create_synthetic_video(tmp_path / "clip.mp4", frame_count=8, fps=10.0, width=WIDTH, height=HEIGHT)
    source = client.post(f"/projects/{project['id']}/sources", json={"path": str(video)}).json()
    process_source_sync(client, project["id"], source["id"], _SteadyDetector(), target_fps=10.0)
    frames = client.get(f"/projects/{project['id']}/frames").json()
    assert frames
    return project, frames


def _save(frame: dict, boxes: list[dict]):
    return client.put(f"/frames/{frame['id']}/annotations", json={"annotations": boxes})


def _box(bbox=None, class_id: int = 4, **attributes) -> dict:
    return {"class_id": class_id, "bbox_json": bbox or CAR, "attributes": attributes}


# --- what the attributes are ------------------------------------------------


def test_the_attribute_definitions_are_served_rather_than_duplicated():
    """One source for what an attribute is. The canvas renders from this
    and the server validates against it, so the UI cannot offer a value
    the save will reject."""
    response = client.get("/annotation-attributes")
    assert response.status_code == 200

    definitions = response.json()
    by_key = {d["key"]: d for d in definitions}
    assert "plate_text" in by_key
    assert by_key["plate_text"]["type"] == "text"
    assert by_key["occluded"]["type"] == "boolean"
    assert by_key["direction"]["type"] == "choice"
    assert by_key["direction"]["options"], "a choice with no options is not choosable"
    for option in by_key["direction"]["options"]:
        assert option["value"] and option["label"]


# --- writing them -----------------------------------------------------------


def test_plate_text_is_a_free_string_and_survives_save_and_reload(queue):
    """The plate-text card in track review typed whatever the reviewer
    read off the plate. This is that, moved onto the box it describes."""
    project, frames = queue
    saved = _save(frames[0], [_box(plate_text="MH 12 AB 1234")])
    assert saved.status_code == 200, saved.text
    assert saved.json()[0]["attributes"]["plate_text"] == "MH 12 AB 1234"

    reloaded = client.get(f"/frames/{frames[0]['id']}/annotations").json()
    assert reloaded[0]["attributes"]["plate_text"] == "MH 12 AB 1234"


def test_every_kind_of_attribute_round_trips(queue):
    project, frames = queue
    saved = _save(
        frames[0],
        [_box(plate_text="DL 3C 1234", colour="white", direction="incoming", occluded=True, night=False)],
    )
    assert saved.status_code == 200, saved.text

    attributes = client.get(f"/frames/{frames[0]['id']}/annotations").json()[0]["attributes"]
    assert attributes == {
        "plate_text": "DL 3C 1234",
        "colour": "white",
        "direction": "incoming",
        "occluded": True,
        "night": False,
    }


def test_editing_a_box_keeps_the_attributes_it_was_not_asked_about(queue):
    """A box echoed back with its id is the same row. Its attributes come
    back with it, so nudging a box does not wipe the plate someone typed."""
    project, frames = queue
    first = _save(frames[0], [_box(plate_text="KA 01 AA 1111")]).json()[0]

    moved = _save(
        frames[0],
        [{"id": first["id"], "class_id": 4, "bbox_json": TRUCK, "attributes": first["attributes"]}],
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()[0]["id"] == first["id"]
    assert moved.json()[0]["attributes"]["plate_text"] == "KA 01 AA 1111"


def test_attributes_are_optional(queue):
    """A box with nothing said about it is the normal case, not an error."""
    project, frames = queue
    saved = _save(frames[0], [{"class_id": 4, "bbox_json": CAR}])
    assert saved.status_code == 200, saved.text
    assert saved.json()[0]["attributes"] == {}


def test_clearing_an_attribute_removes_it_rather_than_storing_a_blank(queue):
    """Empty string and "not set" are the same thing to a human, and
    letting both exist means every reader has to check for both."""
    project, frames = queue
    first = _save(frames[0], [_box(plate_text="TN 09 BC 4321", colour="red")]).json()[0]

    cleared = _save(
        frames[0],
        [{"id": first["id"], "class_id": 4, "bbox_json": CAR, "attributes": {"plate_text": "", "colour": None}}],
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()[0]["attributes"] == {}


def test_plate_text_is_trimmed(queue):
    project, frames = queue
    saved = _save(frames[0], [_box(plate_text="  UP 16 CD 0007  ")])
    assert saved.json()[0]["attributes"]["plate_text"] == "UP 16 CD 0007"


# --- refusing nonsense ------------------------------------------------------


def test_an_unknown_attribute_is_refused(queue):
    """Typos are the whole reason this is checked. A silently accepted
    "plate_txt" is a field nothing will ever read again."""
    project, frames = queue
    response = _save(frames[0], [_box(plate_txt="MH 12 AB 1234")])

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_box"
    assert "plate_txt" in response.json()["message"]


def test_a_choice_outside_its_options_is_refused(queue):
    project, frames = queue
    response = _save(frames[0], [_box(direction="sideways")])

    assert response.status_code == 400
    assert "sideways" in response.json()["message"]


def test_a_boolean_that_is_not_a_boolean_is_refused(queue):
    """JSON "true" is a string. Storing it would make every later read of
    this field quietly truthy."""
    project, frames = queue
    response = _save(frames[0], [_box(occluded="true")])

    assert response.status_code == 400
    assert "occluded" in response.json()["message"]


def test_plate_text_longer_than_a_plate_is_refused(queue):
    project, frames = queue
    response = _save(frames[0], [_box(plate_text="X" * 200)])

    assert response.status_code == 400
    assert "plate_text" in response.json()["message"]


def test_a_bad_attribute_leaves_the_previous_boxes_untouched(queue):
    """Whole-set replacement is all-or-nothing, and attributes are part
    of the set - a rejected save must not have deleted anything."""
    project, frames = queue
    _save(frames[0], [_box(plate_text="GJ 05 XY 9999")])

    rejected = _save(frames[0], [_box(bbox=TRUCK, direction="upwards")])
    assert rejected.status_code == 400

    still_there = client.get(f"/frames/{frames[0]['id']}/annotations").json()
    assert len(still_there) == 1
    assert still_there[0]["attributes"]["plate_text"] == "GJ 05 XY 9999"


# --- and out into the dataset -----------------------------------------------


def test_attributes_reach_the_exported_manifest(queue):
    """Recording plate text that never leaves the database would make the
    panel a diary rather than a labelling tool. The label file has no
    room for it; the manifest is where it goes."""
    project, frames = queue
    _save(frames[0], [_box(plate_text="RJ 14 CV 0002", colour="white", occluded=True)])

    created = client.post(f"/projects/{project['id']}/dataset-versions", json={})
    assert created.status_code == 201, created.text

    export_dir = Path(project["workspace_path"]) / "exports" / "v1"
    manifest = json.loads((export_dir / "manifest.json").read_text(encoding="utf-8"))
    obj = manifest["items"][0]["objects"][0]
    assert obj["attributes"] == {"plate_text": "RJ 14 CV 0002", "colour": "white", "occluded": True}


def test_a_frame_with_no_attributes_still_exports_cleanly(queue):
    project, frames = queue
    _save(frames[0], [{"class_id": 4, "bbox_json": CAR}])

    created = client.post(f"/projects/{project['id']}/dataset-versions", json={}).json()
    assert created["validation"]["valid"] is True, created["validation"]["errors"]

    export_dir = Path(project["workspace_path"]) / "exports" / "v1"
    manifest = json.loads((export_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["items"][0]["objects"][0]["attributes"] == {}
