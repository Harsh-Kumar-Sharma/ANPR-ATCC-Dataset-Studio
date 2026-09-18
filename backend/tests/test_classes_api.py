from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _anpr_project(name: str) -> dict:
    return client.post("/projects", json={"name": name, "class_preset": "anpr-v1"}).json()


def test_the_editor_lists_the_projects_classes():
    project = _anpr_project("Editor List Project")

    listed = client.get(f"/projects/{project['id']}/classes").json()

    assert [c["name"] for c in listed] == ["vehicle", "number_plate"]
    assert [c["class_id"] for c in listed] == [1, 2]


def test_adding_a_class_through_the_editor():
    project = _anpr_project("Editor Add Project")

    created = client.post(f"/projects/{project['id']}/classes", json={"name": "truck"})

    assert created.status_code == 201
    assert created.json()["name"] == "truck"
    assert [c["name"] for c in client.get(f"/projects/{project['id']}/classes").json()] == [
        "vehicle",
        "number_plate",
        "truck",
    ]


def test_a_renamed_class_shows_up_in_the_review_ui():
    """Ticket 06's done-when: the review UI reads the project's classes, so
    a rename reaches it with no code change."""
    project = _anpr_project("Editor Rename Project")

    renamed = client.patch(f"/projects/{project['id']}/classes/2", json={"name": "plate"})
    assert renamed.status_code == 200

    # /class-schema is what the review UI actually calls.
    schema = client.get(f"/projects/{project['id']}/class-schema").json()
    assert {"id": 2, "name": "plate"} in schema


def test_a_duplicate_name_is_refused_with_something_the_editor_can_show():
    project = _anpr_project("Editor Duplicate Project")

    response = client.post(f"/projects/{project['id']}/classes", json={"name": "vehicle"})

    assert response.status_code == 409
    assert response.json()["code"] == "duplicate_class_name"
    assert "vehicle" in response.json()["message"]


def test_a_duplicate_name_is_refused_regardless_of_case_or_padding():
    project = _anpr_project("Editor Case Project")

    response = client.post(f"/projects/{project['id']}/classes", json={"name": "  VEHICLE "})

    assert response.status_code == 409
    assert response.json()["code"] == "duplicate_class_name"


def test_renaming_onto_an_existing_name_is_refused():
    project = _anpr_project("Editor Rename Clash Project")

    response = client.patch(f"/projects/{project['id']}/classes/2", json={"name": "vehicle"})

    assert response.status_code == 409
    assert response.json()["code"] == "duplicate_class_name"


def test_a_blank_name_is_rejected_before_it_reaches_the_database():
    project = _anpr_project("Editor Blank Project")

    assert client.post(f"/projects/{project['id']}/classes", json={"name": ""}).status_code == 422


def test_renaming_a_class_that_does_not_exist_is_a_404():
    project = _anpr_project("Editor Missing Project")

    response = client.patch(f"/projects/{project['id']}/classes/99", json={"name": "ghost"})

    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


def test_usage_reports_how_many_labels_a_class_holds():
    project = _anpr_project("Editor Usage Project")

    usage = client.get(f"/projects/{project['id']}/classes/2/usage").json()

    assert usage == {"class_id": 2, "label_count": 0}


def test_an_unused_class_can_be_deleted():
    project = _anpr_project("Editor Delete Project")

    assert client.delete(f"/projects/{project['id']}/classes/2").status_code == 204
    assert [c["name"] for c in client.get(f"/projects/{project['id']}/classes").json()] == ["vehicle"]


def test_a_class_in_use_refuses_deletion_rather_than_orphaning_labels(tmp_path):
    """Until ticket 07's remap conversation exists, refusing is the only
    honest answer - orphaned labels are the easiest way to silently
    poison a dataset."""
    from tests.job_execution import process_source_sync, tracks_for_run
    from tests.stub_detector import StubDetector
    from tests.video_factory import create_synthetic_video

    project = _anpr_project("Editor In Use Project")
    video = create_synthetic_video(tmp_path / "inuse.mp4", frame_count=30, fps=10.0)
    source = client.post(f"/projects/{project['id']}/sources", json={"path": str(video)}).json()
    submitted = process_source_sync(client, project["id"], source["id"], StubDetector())
    track = tracks_for_run(client, project["id"], submitted["run_id"])[0]
    frame_id = client.get(f"/tracks/{track['id']}").json()["frames"][0]["id"]
    client.put(
        f"/tracks/{track['id']}/review",
        json={"frame_candidate_id": frame_id, "decision": "accepted", "class_id": 2},
    )

    usage = client.get(f"/projects/{project['id']}/classes/2/usage").json()
    assert usage["label_count"] == 1

    response = client.delete(f"/projects/{project['id']}/classes/2")
    assert response.status_code == 409
    assert response.json()["code"] == "class_in_use"
    assert "1 label" in response.json()["message"]

    # The class is still there, and so is the label.
    assert any(c["class_id"] == 2 for c in client.get(f"/projects/{project['id']}/classes").json())


def test_class_edits_do_not_leak_between_projects():
    first = _anpr_project("Editor Isolation A")
    second = _anpr_project("Editor Isolation B")

    client.patch(f"/projects/{first['id']}/classes/1", json={"name": "car"})

    assert [c["name"] for c in client.get(f"/projects/{second['id']}/classes").json()] == [
        "vehicle",
        "number_plate",
    ]
