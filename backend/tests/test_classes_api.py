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


def test_a_whitespace_only_name_is_refused_with_a_reason():
    """It clears pydantic's min_length but is still not a name. It used
    to fall through to the catch-all handler as an anonymous 500."""
    project = _anpr_project("Editor Whitespace Project")

    response = client.post(f"/projects/{project['id']}/classes", json={"name": "   "})

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_class_name"


def test_usage_for_a_class_that_does_not_exist_is_a_404():
    """Otherwise it cheerfully reports zero labels for a class that
    never existed, which reads as "safe to delete"."""
    project = _anpr_project("Editor Usage Missing Project")

    response = client.get(f"/projects/{project['id']}/classes/99/usage")

    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


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

    response = client.delete(f"/projects/{project['id']}/classes/2")

    assert response.status_code == 200
    assert response.json() == {"class_id": 2, "remapped": 0, "deleted_labels": 0}
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
    assert "1 annotation" in response.json()["message"]

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


# --- ticket 07: deleting a class that is in use ----------------------------


def _project_with_labelled_track(tmp_path, name: str, class_id: int) -> tuple[dict, dict]:
    """An ANPR project with one accepted label on ``class_id``."""
    from tests.job_execution import process_source_sync, tracks_for_run
    from tests.stub_detector import StubDetector
    from tests.video_factory import create_synthetic_video

    project = _anpr_project(name)
    video = create_synthetic_video(tmp_path / f"{name}.mp4", frame_count=30, fps=10.0)
    source = client.post(f"/projects/{project['id']}/sources", json={"path": str(video)}).json()
    submitted = process_source_sync(client, project["id"], source["id"], StubDetector())
    track = tracks_for_run(client, project["id"], submitted["run_id"])[0]
    frame_id = client.get(f"/tracks/{track['id']}").json()["frames"][0]["id"]
    client.put(
        f"/tracks/{track['id']}/review",
        json={"frame_candidate_id": frame_id, "decision": "accepted", "class_id": class_id},
    )
    return project, track


def test_deleting_a_class_in_use_prompts_with_the_actual_count(tmp_path):
    """The refusal carries the number, so the editor can ask "these N
    labels use this class - move them where, or delete them?"."""
    project, _ = _project_with_labelled_track(tmp_path, "Prompt Count Project", class_id=2)

    response = client.delete(f"/projects/{project['id']}/classes/2")

    assert response.status_code == 409
    assert response.json()["code"] == "class_in_use"
    assert "1 annotation" in response.json()["message"]
    assert client.get(f"/projects/{project['id']}/classes/2/usage").json()["label_count"] == 1


def test_choosing_a_target_class_remaps_every_label(tmp_path):
    project, track = _project_with_labelled_track(tmp_path, "Remap Project", class_id=2)

    response = client.delete(f"/projects/{project['id']}/classes/2", params={"remap_to": 1})

    assert response.status_code == 200
    assert response.json() == {"class_id": 2, "remapped": 1, "deleted_labels": 0}
    assert client.get(f"/tracks/{track['id']}/annotation").json()["class_id"] == 1
    assert [c["class_id"] for c in client.get(f"/projects/{project['id']}/classes").json()] == [1]


def test_choosing_deletion_removes_the_labels_with_the_class(tmp_path):
    project, track = _project_with_labelled_track(tmp_path, "Delete Labels Project", class_id=2)

    response = client.delete(f"/projects/{project['id']}/classes/2", params={"delete_labels": "true"})

    assert response.status_code == 200
    assert response.json() == {"class_id": 2, "remapped": 0, "deleted_labels": 1}
    assert client.get(f"/tracks/{track['id']}/annotation").status_code == 404
    # The track no longer claims a review it does not have.
    listed = client.get(f"/projects/{project['id']}/tracks").json()
    assert next(t for t in listed if t["id"] == track["id"])["review_status"] == "unreviewed"


def test_merging_two_classes_uses_the_same_path(tmp_path):
    project, track = _project_with_labelled_track(tmp_path, "Merge Project", class_id=1)

    # "Merge vehicle into number_plate" is "delete vehicle, remapping to 2".
    response = client.delete(f"/projects/{project['id']}/classes/1", params={"remap_to": 2})

    assert response.status_code == 200
    assert client.get(f"/tracks/{track['id']}/annotation").json()["class_id"] == 2
    assert [c["name"] for c in client.get(f"/projects/{project['id']}/classes").json()] == ["number_plate"]


def test_remapping_onto_a_class_that_does_not_exist_is_refused(tmp_path):
    project, track = _project_with_labelled_track(tmp_path, "Remap Missing Project", class_id=2)

    response = client.delete(f"/projects/{project['id']}/classes/2", params={"remap_to": 99})

    assert response.status_code == 404
    # Nothing moved, nothing deleted.
    assert client.get(f"/tracks/{track['id']}/annotation").json()["class_id"] == 2
    assert any(c["class_id"] == 2 for c in client.get(f"/projects/{project['id']}/classes").json())


def test_merging_a_class_into_itself_is_refused(tmp_path):
    project, _ = _project_with_labelled_track(tmp_path, "Self Merge Project", class_id=2)

    response = client.delete(f"/projects/{project['id']}/classes/2", params={"remap_to": 2})

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_remap"


def test_asking_for_both_options_is_refused(tmp_path):
    project, _ = _project_with_labelled_track(tmp_path, "Both Options Project", class_id=2)

    response = client.delete(
        f"/projects/{project['id']}/classes/2", params={"remap_to": 1, "delete_labels": "true"}
    )

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_remap"


def test_a_remapped_label_exports_under_its_new_class(tmp_path):
    """The end of the seam: a remap is only real if the next export
    reflects it."""
    project, _ = _project_with_labelled_track(tmp_path, "Remap Export Project", class_id=2)
    client.delete(f"/projects/{project['id']}/classes/2", params={"remap_to": 1})

    created = client.post(f"/projects/{project['id']}/dataset-versions", json={})

    assert created.status_code == 201, created.text
    assert created.json()["validation"]["valid"] is True
    import json
    from pathlib import Path

    manifest = json.loads(
        (Path(project["workspace_path"]) / "exports" / "v1" / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["items"][0]["objects"][0]["class_name"] == "vehicle"
