"""Naming a source.

Every live source from one camera read "160.187.179.196 · ch 1", so
choosing one to hand out as a task meant guessing by frame count.
"""

from fastapi.testclient import TestClient

from app.db.models.labeling_task import LabelingTask
from app.db.models.project import Project
from app.db.models.source import Source
from app.db.session import SessionLocal
from app.main import app

client = TestClient(app)


def _source() -> tuple[str, str]:
    with SessionLocal() as db:
        project = Project(name="Names", workspace_path="", class_schema_version="anpr-v1")
        db.add(project)
        db.flush()
        source = Source(
            project_id=project.id,
            type="rtsp",
            path_or_uri="rtsp://admin:pw@160.187.179.196:9001/Streaming/channels/1",
            fps=10,
            width=0,
            height=0,
            duration_ms=0,
            frame_count=0,
        )
        db.add(source)
        db.commit()
        return project.id, source.id


def test_a_source_has_no_name_until_given_one():
    project_id, source_id = _source()
    listed = client.get(f"/projects/{project_id}/sources").json()
    assert listed[0]["name"] is None


def test_naming_a_source_shows_everywhere_it_is_listed():
    project_id, source_id = _source()
    response = client.patch(f"/projects/{project_id}/sources/{source_id}", json={"name": "  Toll plaza night  "})
    assert response.status_code == 200
    assert response.json()["name"] == "Toll plaza night"

    assert client.get(f"/projects/{project_id}/sources").json()[0]["name"] == "Toll plaza night"
    queues = client.get(f"/projects/{project_id}/frames/by-source").json()
    assert queues[0]["name"] == "Toll plaza night"


def test_a_blank_name_clears_it():
    project_id, source_id = _source()
    client.patch(f"/projects/{project_id}/sources/{source_id}", json={"name": "Lane 2"})
    response = client.patch(f"/projects/{project_id}/sources/{source_id}", json={"name": "   "})
    assert response.json()["name"] is None


def test_a_task_carries_its_source_name():
    project_id, source_id = _source()
    client.patch(f"/projects/{project_id}/sources/{source_id}", json={"name": "Entry gate"})
    with SessionLocal() as db:
        db.add(LabelingTask(project_id=project_id, source_id=source_id, status="assigned"))
        db.commit()
    tasks = client.get(f"/projects/{project_id}/tasks").json()
    assert tasks[0]["source_name"] == "Entry gate"


def test_a_name_that_is_too_long_is_refused():
    project_id, source_id = _source()
    response = client.patch(f"/projects/{project_id}/sources/{source_id}", json={"name": "x" * 129})
    assert response.status_code == 409
