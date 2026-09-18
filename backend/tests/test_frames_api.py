from fastapi.testclient import TestClient

from app.main import app
from tests.job_execution import process_source_sync
from tests.stub_detector import StubDetector
from tests.video_factory import create_synthetic_video

client = TestClient(app)


def _project_with_frames(tmp_path, name: str, *, preset: str = "anpr-v1", frame_count: int = 20) -> tuple[dict, list[dict]]:
    """A processed project: detection has run, so every observed frame is
    in the queue."""
    project = client.post("/projects", json={"name": name, "class_preset": preset}).json()
    video = create_synthetic_video(tmp_path / f"{name}.mp4", frame_count=frame_count, fps=10.0)
    source = client.post(f"/projects/{project['id']}/sources", json={"path": str(video)}).json()
    process_source_sync(client, project["id"], source["id"], StubDetector())
    queue = client.get(f"/projects/{project['id']}/frames").json()
    return project, queue


def _box(class_id, x1, y1, x2, y2, **attributes) -> dict:
    return {"class_id": class_id, "bbox_json": [x1, y1, x2, y2], "attributes": attributes}


# --- the queue ---------------------------------------------------------------


def test_detection_fills_the_queue_with_pending_frames(tmp_path):
    project, queue = _project_with_frames(tmp_path, "Queue Fill Project")

    assert len(queue) > 0
    assert all(f["status"] == "pending" for f in queue)
    assert all(f["width"] > 0 and f["height"] > 0 for f in queue)
    indices = [f["frame_index"] for f in queue]
    assert indices == sorted(indices)


def test_the_queue_is_empty_for_a_project_nothing_has_been_run_on():
    project = client.post("/projects", json={"name": "Empty Queue Project"}).json()

    assert client.get(f"/projects/{project['id']}/frames").json() == []


def test_the_queue_can_be_filtered_by_status(tmp_path):
    project, queue = _project_with_frames(tmp_path, "Queue Filter Project")
    first = queue[0]

    client.put(f"/frames/{first['id']}/annotations", json={"annotations": []})

    labeled = client.get(f"/projects/{project['id']}/frames", params={"status": "labeled"}).json()
    pending = client.get(f"/projects/{project['id']}/frames", params={"status": "pending"}).json()
    assert [f["id"] for f in labeled] == [first["id"]]
    assert first["id"] not in [f["id"] for f in pending]
    assert len(pending) == len(queue) - 1


def test_an_unknown_frame_is_a_404():
    assert client.get("/frames/does-not-exist").status_code == 404
    assert client.get("/frames/does-not-exist/annotations").status_code == 404
    assert client.put("/frames/does-not-exist/annotations", json={"annotations": []}).status_code == 404


# --- the tracer bullet: draw a box, save, reload -------------------------------


def test_a_saved_box_is_still_there_when_the_frame_is_reopened(tmp_path):
    """Ticket 08's done-when, end to end over HTTP."""
    _, queue = _project_with_frames(tmp_path, "Round Trip Project")
    frame = queue[0]

    saved = client.put(f"/frames/{frame['id']}/annotations", json={"annotations": [_box(1, 5, 5, 30, 25)]})
    assert saved.status_code == 200, saved.text
    assert len(saved.json()) == 1
    assert saved.json()[0]["frame_id"] == frame["id"]
    assert saved.json()[0]["frame_candidate_id"] is None

    reopened = client.get(f"/frames/{frame['id']}/annotations").json()
    assert [a["bbox_json"] for a in reopened] == [[5.0, 5.0, 30.0, 25.0]]
    assert reopened[0]["class_id"] == 1
    assert client.get(f"/frames/{frame['id']}").json()["status"] == "labeled"


def test_saving_replaces_the_set_so_a_removed_box_is_gone(tmp_path):
    _, queue = _project_with_frames(tmp_path, "Replace Set Project")
    frame = queue[0]

    client.put(
        f"/frames/{frame['id']}/annotations",
        json={"annotations": [_box(1, 0, 0, 10, 10), _box(2, 20, 20, 30, 30)]},
    )
    assert len(client.get(f"/frames/{frame['id']}/annotations").json()) == 2

    client.put(f"/frames/{frame['id']}/annotations", json={"annotations": [_box(2, 20, 20, 30, 30)]})

    remaining = client.get(f"/frames/{frame['id']}/annotations").json()
    assert [a["bbox_json"] for a in remaining] == [[20.0, 20.0, 30.0, 30.0]]


def test_many_boxes_on_one_frame(tmp_path):
    _, queue = _project_with_frames(tmp_path, "Many Boxes Project")
    frame = queue[0]

    response = client.put(
        f"/frames/{frame['id']}/annotations",
        json={"annotations": [_box(1, 0, 0, 10, 10), _box(1, 12, 0, 22, 10), _box(2, 30, 30, 40, 40)]},
    )

    assert response.status_code == 200
    assert len(response.json()) == 3


def test_attributes_survive_the_round_trip(tmp_path):
    _, queue = _project_with_frames(tmp_path, "Attributes Project")
    frame = queue[0]

    client.put(
        f"/frames/{frame['id']}/annotations",
        json={"annotations": [_box(2, 0, 0, 10, 10, plate_text="KA05MN1234", night=True)]},
    )

    assert client.get(f"/frames/{frame['id']}/annotations").json()[0]["attributes"] == {
        "plate_text": "KA05MN1234",
        "night": True,
    }


# --- validation --------------------------------------------------------------


def test_a_box_outside_the_frame_is_refused_and_the_previous_set_survives(tmp_path):
    _, queue = _project_with_frames(tmp_path, "Bad Box Project")
    frame = queue[0]
    client.put(f"/frames/{frame['id']}/annotations", json={"annotations": [_box(1, 0, 0, 10, 10)]})

    response = client.put(
        f"/frames/{frame['id']}/annotations",
        json={"annotations": [_box(1, 20, 20, 30, 30), _box(1, 0, 0, 99999, 10)]},
    )

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_box"
    assert "Box 1" in response.json()["message"]
    assert [a["bbox_json"] for a in client.get(f"/frames/{frame['id']}/annotations").json()] == [[0.0, 0.0, 10.0, 10.0]]


def test_a_class_this_project_does_not_have_is_refused(tmp_path):
    """Validated against *this* project's classes: an ANPR project has two."""
    _, queue = _project_with_frames(tmp_path, "Bad Class Project")
    frame = queue[0]

    response = client.put(f"/frames/{frame['id']}/annotations", json={"annotations": [_box(20, 0, 0, 10, 10)]})

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_box"


def test_a_malformed_box_is_rejected_before_it_reaches_the_service(tmp_path):
    _, queue = _project_with_frames(tmp_path, "Malformed Box Project")
    frame = queue[0]

    response = client.put(
        f"/frames/{frame['id']}/annotations", json={"annotations": [{"class_id": 1, "bbox_json": [0, 0, 10]}]}
    )

    assert response.status_code == 422


# --- the image -----------------------------------------------------------------


def test_the_frame_image_is_served_as_a_jpeg(tmp_path):
    _, queue = _project_with_frames(tmp_path, "Frame Image Project")
    frame = queue[0]

    response = client.get(f"/frames/{frame['id']}/image")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/jpeg")
    assert response.content[:2] == b"\xff\xd8", "JPEG magic bytes"

    # Second request is served from the decoded file, not re-decoded.
    again = client.get(f"/frames/{frame['id']}/image")
    assert again.content == response.content


# --- the invariant from ticket 07 holds through the new path -------------------


def test_a_canvas_box_stops_its_class_being_deleted(tmp_path):
    """Canvas labels have no frame candidate. If "whose annotation is
    this" still went through the candidate, they would be invisible to
    the check that protects a class in use - and a class could be
    deleted from under them."""
    project, queue = _project_with_frames(tmp_path, "Canvas Blocks Delete Project")
    frame = queue[0]
    client.put(f"/frames/{frame['id']}/annotations", json={"annotations": [_box(2, 0, 0, 10, 10)]})

    response = client.delete(f"/projects/{project['id']}/classes/2")

    assert response.status_code == 409
    assert response.json()["code"] == "class_in_use"
    assert client.get(f"/projects/{project['id']}/classes/2/usage").json()["label_count"] == 1


def test_remapping_a_class_reaches_canvas_boxes_too(tmp_path):
    project, queue = _project_with_frames(tmp_path, "Canvas Remap Project")
    frame = queue[0]
    client.put(f"/frames/{frame['id']}/annotations", json={"annotations": [_box(2, 0, 0, 10, 10)]})

    client.delete(f"/projects/{project['id']}/classes/2", params={"remap_to": 1})

    assert client.get(f"/frames/{frame['id']}/annotations").json()[0]["class_id"] == 1


# --- ids survive a save ------------------------------------------------------------


def test_a_box_sent_back_with_its_id_keeps_that_id(tmp_path):
    _, queue = _project_with_frames(tmp_path, "Echo Id Project")
    frame = queue[0]
    first = client.put(f"/frames/{frame['id']}/annotations", json={"annotations": [_box(1, 0, 0, 10, 10)]}).json()
    box_id = first[0]["id"]

    again = client.put(
        f"/frames/{frame['id']}/annotations",
        json={"annotations": [{"id": box_id, "class_id": 2, "bbox_json": [0, 0, 10, 10], "attributes": {}}]},
    ).json()

    assert [a["id"] for a in again] == [box_id]
    assert again[0]["class_id"] == 2


def test_a_stale_id_is_a_conflict_the_canvas_can_act_on(tmp_path):
    _, queue = _project_with_frames(tmp_path, "Stale Id Project")
    frame = queue[0]
    first = client.put(f"/frames/{frame['id']}/annotations", json={"annotations": [_box(1, 0, 0, 10, 10)]}).json()
    client.put(f"/frames/{frame['id']}/annotations", json={"annotations": []})

    response = client.put(
        f"/frames/{frame['id']}/annotations",
        json={"annotations": [{"id": first[0]["id"], "class_id": 1, "bbox_json": [0, 0, 10, 10], "attributes": {}}]},
    )

    assert response.status_code == 409
    assert response.json()["code"] == "stale_box"


def test_an_unknown_status_filter_is_a_400_not_an_empty_list(tmp_path):
    project, _ = _project_with_frames(tmp_path, "Status Filter 400 Project")

    response = client.get(f"/projects/{project['id']}/frames", params={"status": "done"})

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_filter"
