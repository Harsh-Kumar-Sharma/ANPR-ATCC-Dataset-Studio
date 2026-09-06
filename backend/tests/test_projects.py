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
    assert body["class_schema_version"] == "v1"

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
