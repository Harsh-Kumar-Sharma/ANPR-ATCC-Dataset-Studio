from pathlib import Path

from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import app

client = TestClient(app)


def test_create_project_creates_db_row_and_workspace():
    response = client.post("/projects", json={"name": "Gantry Site A"})
    assert response.status_code == 201
    body = response.json()

    assert body["name"] == "Gantry Site A"
    assert body["class_schema_version"] == "atcc-v1"

    workspace = Path(body["workspace_path"])
    assert workspace.exists()
    for subdir in ("source", "cache/thumbnails", "derived/tracks", "derived/plates", "review", "exports", "logs"):
        assert (workspace / subdir).is_dir()
    assert (workspace / "project.json").is_file()


def test_list_and_get_project_round_trip():
    created = client.post("/projects", json={"name": "Gantry Site B"}).json()

    listed = client.get("/projects").json()
    assert any(p["id"] == created["id"] for p in listed)

    fetched = client.get(f"/projects/{created['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["id"] == created["id"]


def test_get_unknown_project_returns_404():
    response = client.get("/projects/does-not-exist")
    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


def test_creating_project_twice_with_same_name_does_not_collide():
    first = client.post("/projects", json={"name": "Duplicate Name"}).json()
    second = client.post("/projects", json={"name": "Duplicate Name"}).json()
    assert first["id"] != second["id"]
    assert first["workspace_path"] != second["workspace_path"]


def test_a_new_project_gets_its_own_copy_of_the_default_classes():
    project = client.post("/projects", json={"name": "Default Preset Project"}).json()

    classes = client.get(f"/projects/{project['id']}/class-schema").json()

    assert len(classes) == 20
    assert {"id": 4, "name": "Car/Jeep/Van"} in classes


def test_an_anpr_project_gets_the_two_classes_a_plate_detector_needs():
    """The reason presets exist: twenty traffic-survey categories are
    noise in a number-plate project."""
    project = client.post("/projects", json={"name": "ANPR Project", "class_preset": "anpr-v1"}).json()

    classes = client.get(f"/projects/{project['id']}/class-schema").json()

    assert [c["name"] for c in classes] == ["vehicle", "number_plate"]
    assert project["class_schema_version"] == "anpr-v1"


def test_a_blank_project_starts_with_no_classes():
    project = client.post("/projects", json={"name": "Blank Project", "class_preset": "blank"}).json()

    assert client.get(f"/projects/{project['id']}/class-schema").json() == []


def test_renaming_one_projects_class_leaves_every_other_project_alone():
    """Presets are copied, never shared live - this is the property that
    makes them safe to edit."""
    from app.db.models import ClassDefinition
    from app.db.session import SessionLocal

    first = client.post("/projects", json={"name": "Isolation One"}).json()
    second = client.post("/projects", json={"name": "Isolation Two"}).json()

    with SessionLocal() as db:
        row = (
            db.query(ClassDefinition)
            .filter(ClassDefinition.project_id == first["id"], ClassDefinition.class_id == 1)
            .one()
        )
        row.name = "Motorbike"
        db.commit()

    assert {"id": 1, "name": "Motorbike"} in client.get(f"/projects/{first['id']}/class-schema").json()
    assert {"id": 1, "name": "Two Wheeler"} in client.get(f"/projects/{second['id']}/class-schema").json()


def test_a_renamed_class_shows_up_where_labelling_happens():
    """Ticket 05's done-when: the review UI reads classes from the project,
    so a rename is visible without a code change."""
    from app.db.models import ClassDefinition
    from app.db.session import SessionLocal

    project = client.post("/projects", json={"name": "Rename Visible Project"}).json()

    with SessionLocal() as db:
        row = (
            db.query(ClassDefinition)
            .filter(ClassDefinition.project_id == project["id"], ClassDefinition.class_id == 4)
            .one()
        )
        row.name = "Car (renamed)"
        db.commit()

    served = client.get(f"/projects/{project['id']}/class-schema").json()
    assert {"id": 4, "name": "Car (renamed)"} in served


def test_an_unknown_preset_is_rejected_without_creating_a_project():
    """A project that exists with no usable classes is worse than a
    rejected request."""
    before = len(client.get("/projects").json())

    response = client.post("/projects", json={"name": "Typo Preset", "class_preset": "atcc-v99"})

    assert response.status_code == 400
    assert response.json()["code"] == "unknown_class_preset"
    assert len(client.get("/projects").json()) == before
