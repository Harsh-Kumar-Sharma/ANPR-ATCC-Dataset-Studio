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
    assert response.json()["code"] == "unknown_frame_status"


# --- what export does with a box nobody has classified yet -----------------------


def test_export_skips_a_canvas_box_with_no_class_rather_than_failing(tmp_path):
    """The exporter drops an annotation whose class it cannot resolve. For
    a box nobody has classified yet that is the right outcome - but until
    now nothing pinned it, so a rewrite could turn it into a crash or,
    worse, a class-0 label. Ticket 12 has to keep this passing."""
    project, queue = _project_with_frames(tmp_path, "Null Class Export Project")

    # Something exportable, written the legacy way...
    track = client.get(f"/projects/{project['id']}/tracks").json()[0]
    frame_id = client.get(f"/tracks/{track['id']}").json()["frames"][0]["id"]
    client.put(
        f"/tracks/{track['id']}/review",
        json={"frame_candidate_id": frame_id, "decision": "accepted", "class_id": 1},
    )
    # ...and a canvas box with no class on some frame.
    unclassified = client.put(
        f"/frames/{queue[-1]['id']}/annotations",
        json={"annotations": [{"id": None, "class_id": None, "bbox_json": [0, 0, 10, 10], "attributes": {}}]},
    ).json()[0]

    created = client.post(f"/projects/{project['id']}/dataset-versions", json={})

    assert created.status_code == 201, created.text
    import json
    from pathlib import Path

    manifest = json.loads(
        (Path(project["workspace_path"]) / "exports" / "v1" / "manifest.json").read_text(encoding="utf-8")
    )
    exported = [obj for item in manifest["items"] for obj in item.get("objects", [])]
    assert all(obj["class_id"] is not None for obj in exported)
    assert unclassified["id"] not in {obj["annotation_id"] for obj in exported}
    # Skipped from the export, not deleted from the frame.
    assert client.get(f"/frames/{queue[-1]['id']}/annotations").json()[0]["id"] == unclassified["id"]


# --- ticket 10: skipping frames and seeing progress -----------------------------


def test_rejecting_a_frame_takes_it_out_of_the_queue(tmp_path):
    project, queue = _project_with_frames(tmp_path, "Reject API Project")
    frame = queue[0]

    rejected = client.put(f"/frames/{frame['id']}/status", json={"status": "rejected"})

    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"
    remaining = client.get(f"/projects/{project['id']}/frames").json()
    assert frame["id"] not in [f["id"] for f in remaining]
    assert len(remaining) == len(queue) - 1
    # Still findable, so the judgement can be undone.
    assert [f["id"] for f in client.get(f"/projects/{project['id']}/frames", params={"status": "rejected"}).json()] == [
        frame["id"]
    ]


def test_a_rejected_frame_can_be_put_back(tmp_path):
    project, queue = _project_with_frames(tmp_path, "Unreject API Project")
    frame = queue[0]
    client.put(f"/frames/{frame['id']}/status", json={"status": "rejected"})

    client.put(f"/frames/{frame['id']}/status", json={"status": "pending"})

    assert frame["id"] in [f["id"] for f in client.get(f"/projects/{project['id']}/frames").json()]


def test_rejecting_keeps_the_boxes_already_drawn(tmp_path):
    _, queue = _project_with_frames(tmp_path, "Reject Keeps Boxes Project")
    frame = queue[0]
    client.put(f"/frames/{frame['id']}/annotations", json={"annotations": [_box(1, 0, 0, 10, 10)]})

    client.put(f"/frames/{frame['id']}/status", json={"status": "rejected"})

    assert len(client.get(f"/frames/{frame['id']}/annotations").json()) == 1


def test_an_unknown_status_is_refused(tmp_path):
    _, queue = _project_with_frames(tmp_path, "Bad Status API Project")

    response = client.put(f"/frames/{queue[0]['id']}/status", json={"status": "done"})

    assert response.status_code == 400
    assert response.json()["code"] == "unknown_frame_status"


def test_progress_reports_labelled_rejected_and_remaining(tmp_path):
    project, queue = _project_with_frames(tmp_path, "Progress API Project")

    before = client.get(f"/projects/{project['id']}/frames/progress").json()
    assert before == {"pending": len(queue), "labeled": 0, "rejected": 0, "total": len(queue)}

    client.put(f"/frames/{queue[0]['id']}/annotations", json={"annotations": []})
    client.put(f"/frames/{queue[1]['id']}/status", json={"status": "rejected"})

    after = client.get(f"/projects/{project['id']}/frames/progress").json()
    assert after == {
        "pending": len(queue) - 2,
        "labeled": 1,
        "rejected": 1,
        "total": len(queue),
    }


def test_a_label_on_a_rejected_frame_does_not_reach_the_dataset(tmp_path):
    """The judgement has to reach the export or it is decorative."""
    project, _ = _project_with_frames(tmp_path, "Rejected Export Project")
    track = client.get(f"/projects/{project['id']}/tracks").json()[0]
    timeline = client.get(f"/tracks/{track['id']}").json()
    client.put(
        f"/tracks/{track['id']}/review",
        json={"frame_candidate_id": timeline["frames"][0]["id"], "decision": "accepted", "class_id": 1},
    )
    annotation = client.get(f"/tracks/{track['id']}/annotation").json()

    client.put(f"/frames/{annotation['frame_id']}/status", json={"status": "rejected"})

    refused = client.post(f"/projects/{project['id']}/dataset-versions", json={})
    assert refused.status_code == 400
    assert refused.json()["code"] == "nothing_to_export"
    # The label itself survives, so putting the frame back restores it.
    assert client.get(f"/tracks/{track['id']}/annotation").status_code == 200


def test_putting_a_labelled_frame_back_reports_it_as_labelled(tmp_path):
    project, queue = _project_with_frames(tmp_path, "Unreject Labelled API Project")
    frame = queue[0]
    client.put(f"/frames/{frame['id']}/annotations", json={"annotations": [_box(1, 0, 0, 10, 10)]})
    client.put(f"/frames/{frame['id']}/status", json={"status": "rejected"})

    restored = client.put(f"/frames/{frame['id']}/status", json={"status": "pending"})

    assert restored.json()["status"] == "labeled"
    progress = client.get(f"/projects/{project['id']}/frames/progress").json()
    assert progress["labeled"] == 1
    assert progress["rejected"] == 0
