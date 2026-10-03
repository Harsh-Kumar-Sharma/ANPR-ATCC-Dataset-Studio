"""Giving a source's frames to someone to label, and seeing it to done.

An admin assigns; the assignee sees only that work, starts it by
opening a frame, and submits once nothing is pending; an admin
accepts or sends it back.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.db.models.frame import Frame
from app.db.models.labeling_task import LabelingTask
from app.db.models.project import Project
from app.db.models.source import Source
from app.db.models.user import AuthSession, User
from app.db.session import SessionLocal
from app.main import app

client = TestClient(app)
PASSWORD = "correct-horse"


@pytest.fixture(autouse=True)
def _fresh(real_auth):
    with SessionLocal() as db:
        db.execute(delete(LabelingTask))
        db.execute(delete(AuthSession))
        db.execute(delete(User))
        db.commit()
    yield


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _admin() -> str:
    return client.post("/auth/setup", json={"username": "admin", "password": PASSWORD}).json()["token"]


def _user(admin: str, username: str) -> tuple[dict, str]:
    created = client.post("/users", json={"username": username, "password": PASSWORD}, headers=_bearer(admin))
    assert created.status_code == 201, created.text
    token = client.post("/auth/login", json={"username": username, "password": PASSWORD}).json()["token"]
    return created.json(), token


def _project_with_sources(name: str, frames_per_source: int = 3) -> tuple[str, list[str], dict[str, list[str]]]:
    """A project with two sources and pending frames on each, written
    straight to the database - the queue is what is under test here,
    not how frames get made."""
    with SessionLocal() as db:
        project = Project(name=name, workspace_path="", class_schema_version="anpr-v1")
        db.add(project)
        db.flush()
        source_ids, frame_ids = [], {}
        for n in range(2):
            source = Source(
                project_id=project.id,
                type="video",
                path_or_uri=f"{name}-{n}.mp4",
                fps=10,
                width=64,
                height=48,
                duration_ms=0,
                frame_count=0,
            )
            db.add(source)
            db.flush()
            source_ids.append(source.id)
            frame_ids[source.id] = []
            for i in range(frames_per_source):
                frame = Frame(source_id=source.id, frame_index=i, timestamp_ms=i * 100, width=64, height=48)
                db.add(frame)
                db.flush()
                frame_ids[source.id].append(frame.id)
        db.commit()
        return project.id, source_ids, frame_ids


def _assign(admin: str, project_id: str, source_id: str, user_id: str) -> dict:
    response = client.post(
        f"/projects/{project_id}/tasks",
        json={"source_id": source_id, "assignee_id": user_id},
        headers=_bearer(admin),
    )
    assert response.status_code == 201, response.text
    return response.json()


def _finish_frames(token: str, frame_ids: list[str]) -> None:
    for frame_id in frame_ids:
        response = client.put(f"/frames/{frame_id}/status", json={"status": "rejected"}, headers=_bearer(token))
        assert response.status_code == 200, response.text


# --- assigning ---------------------------------------------------------------


def test_an_admin_assigns_a_source_and_the_task_starts_as_assigned():
    admin = _admin()
    ravi, _ = _user(admin, "ravi")
    project_id, sources, _ = _project_with_sources("Assign")

    task = _assign(admin, project_id, sources[0], ravi["id"])

    assert task["status"] == "assigned"
    assert task["assignee"]["username"] == "ravi"
    assert task["progress"]["pending"] == 3


def test_a_source_has_only_one_task():
    admin = _admin()
    ravi, _ = _user(admin, "ravi")
    project_id, sources, _ = _project_with_sources("Once")
    _assign(admin, project_id, sources[0], ravi["id"])

    again = client.post(
        f"/projects/{project_id}/tasks",
        json={"source_id": sources[0], "assignee_id": ravi["id"]},
        headers=_bearer(admin),
    )
    assert again.status_code == 409


def test_a_user_cannot_assign_tasks():
    admin = _admin()
    ravi, token = _user(admin, "ravi")
    project_id, sources, _ = _project_with_sources("No Assign")
    response = client.post(
        f"/projects/{project_id}/tasks",
        json={"source_id": sources[0], "assignee_id": ravi["id"]},
        headers=_bearer(token),
    )
    assert response.status_code == 403


# --- what a user can see -------------------------------------------------------


def test_a_user_sees_only_projects_and_tasks_given_to_them():
    admin = _admin()
    ravi, token = _user(admin, "ravi")
    _, _ = _user(admin, "sita")
    mine, sources, _ = _project_with_sources("Mine")
    _project_with_sources("Not Mine")
    _assign(admin, mine, sources[0], ravi["id"])

    projects = client.get("/projects", headers=_bearer(token)).json()
    assert [p["name"] for p in projects] == ["Mine"]

    tasks = client.get("/tasks/mine", headers=_bearer(token)).json()
    assert [t["source_id"] for t in tasks] == [sources[0]]

    queues = client.get(f"/projects/{mine}/frames/by-source", headers=_bearer(token)).json()
    assert [q["source_id"] for q in queues] == [sources[0]]


def test_a_user_reaches_their_own_frames_and_not_others():
    admin = _admin()
    ravi, token = _user(admin, "ravi")
    project_id, sources, frames = _project_with_sources("Frames")
    _assign(admin, project_id, sources[0], ravi["id"])

    own = client.get(f"/projects/{project_id}/frames", params={"source_id": sources[0]}, headers=_bearer(token))
    assert own.status_code == 200
    assert len(own.json()) == 3

    other = client.get(f"/projects/{project_id}/frames", params={"source_id": sources[1]}, headers=_bearer(token))
    assert other.status_code == 403
    whole = client.get(f"/projects/{project_id}/frames", headers=_bearer(token))
    assert whole.status_code == 403

    assert client.get(f"/frames/{frames[sources[0]][0]}", headers=_bearer(token)).status_code == 200
    assert client.get(f"/frames/{frames[sources[1]][0]}", headers=_bearer(token)).status_code == 403


def test_a_user_cannot_reach_the_admin_modules():
    admin = _admin()
    ravi, token = _user(admin, "ravi")
    project_id, sources, _ = _project_with_sources("Admin Only")
    _assign(admin, project_id, sources[0], ravi["id"])

    for path in (
        f"/projects/{project_id}/sources",
        f"/projects/{project_id}/tracks",
        f"/projects/{project_id}/dataset-versions",
        "/jobs",
        "/models",
        "/storage",
    ):
        assert client.get(path, headers=_bearer(token)).status_code == 403, path
    assert client.post("/projects", json={"name": "x"}, headers=_bearer(token)).status_code == 403
    # What labelling needs stays open.
    assert client.get(f"/projects/{project_id}/class-schema", headers=_bearer(token)).status_code == 200
    assert client.get("/annotation-attributes", headers=_bearer(token)).status_code == 200


def test_an_admin_still_reaches_everything():
    admin = _admin()
    project_id, sources, frames = _project_with_sources("Admin Sees")
    assert client.get(f"/projects/{project_id}/frames", headers=_bearer(admin)).status_code == 200
    assert client.get(f"/frames/{frames[sources[1]][0]}", headers=_bearer(admin)).status_code == 200


# --- moving through the states ----------------------------------------------


def test_opening_a_frame_starts_the_task():
    admin = _admin()
    ravi, token = _user(admin, "ravi")
    project_id, sources, frames = _project_with_sources("Starts")
    task = _assign(admin, project_id, sources[0], ravi["id"])

    # An admin looking in does not start it.
    client.get(f"/frames/{frames[sources[0]][0]}", headers=_bearer(admin))
    assert client.get(f"/tasks/{task['id']}", headers=_bearer(admin)).json()["status"] == "assigned"

    client.get(f"/frames/{frames[sources[0]][0]}", headers=_bearer(token))
    assert client.get(f"/tasks/{task['id']}", headers=_bearer(token)).json()["status"] == "in_progress"


def test_submitting_waits_until_nothing_is_pending():
    admin = _admin()
    ravi, token = _user(admin, "ravi")
    project_id, sources, frames = _project_with_sources("Submit")
    task = _assign(admin, project_id, sources[0], ravi["id"])

    early = client.post(f"/tasks/{task['id']}/submit", headers=_bearer(token))
    assert early.status_code == 409
    assert early.json()["code"] == "frames_pending"

    _finish_frames(token, frames[sources[0]])
    submitted = client.post(f"/tasks/{task['id']}/submit", headers=_bearer(token))
    assert submitted.status_code == 200
    assert submitted.json()["status"] == "in_review"


def test_only_the_assignee_submits():
    admin = _admin()
    ravi, _ = _user(admin, "ravi")
    _, sita = _user(admin, "sita")
    project_id, sources, _ = _project_with_sources("Whose")
    task = _assign(admin, project_id, sources[0], ravi["id"])
    # Sita cannot even see it.
    assert client.post(f"/tasks/{task['id']}/submit", headers=_bearer(sita)).status_code == 404


def test_an_admin_rejects_with_a_reason_then_accepts():
    admin = _admin()
    ravi, token = _user(admin, "ravi")
    project_id, sources, frames = _project_with_sources("Review")
    task = _assign(admin, project_id, sources[0], ravi["id"])
    _finish_frames(token, frames[sources[0]])
    client.post(f"/tasks/{task['id']}/submit", headers=_bearer(token))

    no_reason = client.post(f"/tasks/{task['id']}/reject", json={"note": " "}, headers=_bearer(admin))
    assert no_reason.status_code == 409

    rejected = client.post(f"/tasks/{task['id']}/reject", json={"note": "Plates missed"}, headers=_bearer(admin))
    assert rejected.json()["status"] == "in_progress"
    assert rejected.json()["review_note"] == "Plates missed"

    client.post(f"/tasks/{task['id']}/submit", headers=_bearer(token))
    accepted = client.post(f"/tasks/{task['id']}/accept", headers=_bearer(admin))
    assert accepted.json()["status"] == "done"
    assert accepted.json()["review_note"] is None

    reopened = client.post(f"/tasks/{task['id']}/reopen", headers=_bearer(admin))
    assert reopened.json()["status"] == "in_progress"


def test_a_user_cannot_review():
    admin = _admin()
    ravi, token = _user(admin, "ravi")
    project_id, sources, frames = _project_with_sources("No Review")
    task = _assign(admin, project_id, sources[0], ravi["id"])
    _finish_frames(token, frames[sources[0]])
    client.post(f"/tasks/{task['id']}/submit", headers=_bearer(token))
    assert client.post(f"/tasks/{task['id']}/accept", headers=_bearer(token)).status_code == 403


def test_reassigning_moves_the_work_to_someone_else():
    admin = _admin()
    ravi, ravi_token = _user(admin, "ravi")
    sita, sita_token = _user(admin, "sita")
    project_id, sources, frames = _project_with_sources("Reassign")
    task = _assign(admin, project_id, sources[0], ravi["id"])

    client.patch(f"/tasks/{task['id']}", json={"assignee_id": sita["id"]}, headers=_bearer(admin))

    assert client.get(f"/frames/{frames[sources[0]][0]}", headers=_bearer(ravi_token)).status_code == 403
    assert client.get(f"/frames/{frames[sources[0]][0]}", headers=_bearer(sita_token)).status_code == 200


def test_deleting_a_task_leaves_the_frames():
    admin = _admin()
    ravi, _ = _user(admin, "ravi")
    project_id, sources, frames = _project_with_sources("Delete Task")
    task = _assign(admin, project_id, sources[0], ravi["id"])

    assert client.delete(f"/tasks/{task['id']}", headers=_bearer(admin)).status_code == 204
    assert client.get(f"/frames/{frames[sources[0]][0]}", headers=_bearer(admin)).status_code == 200


def test_deleting_the_assignee_leaves_the_task_unassigned():
    admin = _admin()
    ravi, _ = _user(admin, "ravi")
    project_id, sources, _ = _project_with_sources("Orphan")
    task = _assign(admin, project_id, sources[0], ravi["id"])

    client.delete(f"/users/{ravi['id']}", headers=_bearer(admin))
    assert client.get(f"/tasks/{task['id']}", headers=_bearer(admin)).json()["assignee"] is None
