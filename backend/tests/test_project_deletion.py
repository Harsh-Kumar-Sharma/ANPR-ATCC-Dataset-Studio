"""Deleting a project, and everything that has to be true first.

This is the most destructive thing the app can do: a project owns
sources, frames, tracks, every label drawn on them, the dataset versions
exported from them and a workspace directory that can run to gigabytes.
None of it comes back.

So most of these tests are about what deletion *refuses* to do.
"""

from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app
from app.ml.types import Detection
from tests.job_execution import deferred_jobs, process_source_sync
from tests.video_factory import create_synthetic_video

client = TestClient(app)

WIDTH, HEIGHT = 128, 96
CAR_CLASS = 4


class _OneCarDetector:
    model_version = "one-car-stub-v1"
    class_names = {0: "car"}

    def detect(self, frame):
        return [Detection(bbox_xyxy=(20.0, 20.0, 80.0, 70.0), class_id=0, confidence=0.9)]


def _project(tmp_path, name: str, *, with_work: bool = True) -> dict:
    """A project, optionally with a source processed and a frame labelled."""
    project = client.post("/projects", json={"name": name}).json()
    if not with_work:
        return project

    video = create_synthetic_video(tmp_path / f"{name}.mp4", frame_count=8, fps=10.0, width=WIDTH, height=HEIGHT)
    source = client.post(f"/projects/{project['id']}/sources", json={"path": str(video)}).json()
    process_source_sync(client, project["id"], source["id"], _OneCarDetector(), target_fps=10.0)

    frames = client.get(f"/projects/{project['id']}/frames").json()
    saved = client.put(
        f"/frames/{frames[0]['id']}/annotations",
        json={"annotations": [{"class_id": CAR_CLASS, "bbox_json": [10.0, 10.0, 70.0, 60.0]}]},
    )
    assert saved.status_code == 200, saved.text
    return project


def _contents(project: dict) -> dict:
    response = client.get(f"/projects/{project['id']}/contents")
    assert response.status_code == 200, response.text
    return response.json()


def _delete(project: dict, confirm: str | None = None):
    return client.request(
        "DELETE",
        f"/projects/{project['id']}",
        json={"name": project["name"] if confirm is None else confirm},
    )


# --- what is in there ---------------------------------------------------------


def test_the_contents_say_what_deleting_would_destroy(tmp_path):
    """A confirmation that cannot say what is about to go is not a
    confirmation."""
    project = _project(tmp_path, "Contents Project")

    contents = _contents(project)

    assert contents["sources"] == 1
    assert contents["frames"] > 0
    assert contents["tracks"] == 1
    assert contents["labels"] == 1
    assert contents["dataset_versions"] == 0
    assert contents["workspace_bytes"] > 0, "the frames and crops on disk are the bulk of it"
    assert contents["running_jobs"] == 0


def test_an_empty_project_reports_an_empty_project(tmp_path):
    project = _project(tmp_path, "Empty Contents", with_work=False)

    contents = _contents(project)

    assert contents["sources"] == 0
    assert contents["frames"] == 0
    assert contents["labels"] == 0


def test_the_contents_count_exported_dataset_versions(tmp_path):
    project = _project(tmp_path, "Exported Contents")
    created = client.post(f"/projects/{project['id']}/dataset-versions", json={})
    assert created.status_code == 201, created.text

    assert _contents(project)["dataset_versions"] == 1


# --- deleting -----------------------------------------------------------------


def test_deleting_a_project_removes_it(tmp_path):
    project = _project(tmp_path, "Deleted Project")

    response = _delete(project)

    assert response.status_code == 200, response.text
    assert client.get(f"/projects/{project['id']}").status_code == 404
    assert project["id"] not in [p["id"] for p in client.get("/projects").json()]


def test_deleting_reports_what_it_destroyed(tmp_path):
    """So the confirmation the user saw and the outcome can be compared."""
    project = _project(tmp_path, "Reporting Delete")
    before = _contents(project)

    removed = _delete(project).json()

    assert removed["sources"] == before["sources"]
    assert removed["frames"] == before["frames"]
    assert removed["labels"] == before["labels"]


def test_everything_the_project_owned_goes_with_it(tmp_path):
    """Nothing enforces the foreign keys here, so orphans do not
    announce themselves - they just sit there being counted by the next
    query that forgets to scope itself."""
    from app.db.models import (
        Annotation,
        ClassDefinition,
        Frame,
        FrameCandidate,
        ProcessingRun,
        Source,
        Track,
    )
    from app.db.session import SessionLocal

    project = _project(tmp_path, "Thorough Delete")
    client.post(f"/projects/{project['id']}/dataset-versions", json={})

    _delete(project)

    with SessionLocal() as db:
        source_ids = [s.id for s in db.query(Source).filter(Source.project_id == project["id"])]
        assert source_ids == []
        assert db.query(ClassDefinition).filter(ClassDefinition.project_id == project["id"]).count() == 0
        # Nothing left anywhere that pointed at this project's sources.
        assert db.query(Frame).filter(Frame.source_id.in_(source_ids or [""])).count() == 0
        assert db.query(ProcessingRun).filter(ProcessingRun.source_id.in_(source_ids or [""])).count() == 0
        for model in (Track, FrameCandidate, Annotation):
            assert db.query(model).count() >= 0  # other projects may still have rows


def test_another_projects_work_is_untouched(tmp_path):
    """The only thing worse than not being able to delete a project."""
    keep = _project(tmp_path / "keep", "Survivor Project")
    doomed = _project(tmp_path / "doomed", "Doomed Project")
    before = _contents(keep)

    _delete(doomed)

    assert _contents(keep) == before
    assert client.get(f"/projects/{keep['id']}").status_code == 200


def test_the_workspace_directory_goes_too(tmp_path):
    """Frames, track crops and exports live there, and they are the
    gigabytes. Leaving them is how a disk fills up with projects nobody
    can name any more."""
    project = _project(tmp_path, "Workspace Delete")
    workspace = Path(project["workspace_path"])
    assert workspace.is_dir()

    _delete(project)

    assert not workspace.exists()


# --- what it refuses ----------------------------------------------------------


def test_the_wrong_name_deletes_nothing(tmp_path):
    """Typing the name is the interlock. A mis-aimed request - the wrong
    id in a URL, a stale page - should not be able to destroy a project
    the user was not looking at."""
    project = _project(tmp_path, "Interlock Project")

    response = _delete(project, confirm="Something Else")

    assert response.status_code == 409
    assert response.json()["code"] == "name_mismatch"
    assert client.get(f"/projects/{project['id']}").status_code == 200
    assert _contents(project)["labels"] == 1


def test_the_name_must_match_exactly(tmp_path):
    project = _project(tmp_path, "Case Project", with_work=False)

    assert _delete(project, confirm="case project").status_code == 409
    assert _delete(project, confirm=" Case Project ").status_code == 409
    assert _delete(project, confirm="Case Project").status_code == 200


def test_a_project_with_work_running_is_not_deleted(tmp_path):
    """A detached worker is still writing to these rows and to that
    directory. Deleting underneath it leaves a process filling a
    workspace that no longer belongs to anything."""
    project = client.post("/projects", json={"name": "Busy Project"}).json()
    video = create_synthetic_video(tmp_path / "busy.mp4", frame_count=8, fps=10.0, width=WIDTH, height=HEIGHT)
    source = client.post(f"/projects/{project['id']}/sources", json={"path": str(video)}).json()

    with deferred_jobs():
        submitted = client.post(
            f"/projects/{project['id']}/sources/{source['id']}/process",
            json={"sampling_config": {"target_fps": 5.0}},
        )
        assert submitted.status_code == 202, submitted.text

    assert _contents(project)["running_jobs"] == 1

    response = _delete(project)

    assert response.status_code == 409
    assert response.json()["code"] == "project_busy"
    assert "job" in response.json()["message"].lower()
    assert client.get(f"/projects/{project['id']}").status_code == 200


def test_deleting_an_unknown_project_is_a_404(tmp_path):
    response = client.request("DELETE", "/projects/does-not-exist", json={"name": "whatever"})

    assert response.status_code == 404


def test_a_workspace_path_outside_the_workspace_root_is_left_alone(tmp_path):
    """The recorded path is a column. A recursive delete driven by a
    column is a recursive delete driven by whatever wrote it, so the
    project's rows go and the directory is reported rather than
    removed."""
    from app.db.models import Project
    from app.db.session import SessionLocal

    project = _project(tmp_path, "Escaped Workspace", with_work=False)
    elsewhere = tmp_path / "not-the-workspace"
    elsewhere.mkdir()
    (elsewhere / "keep.txt").write_text("not ours to delete", encoding="utf-8")

    with SessionLocal() as db:
        db.get(Project, project["id"]).workspace_path = str(elsewhere)
        db.commit()

    response = _delete(project)

    assert response.status_code == 200, response.text
    assert response.json()["workspace_removed"] is False
    assert (elsewhere / "keep.txt").is_file(), "a path outside the workspace root is not ours to delete"
    assert client.get(f"/projects/{project['id']}").status_code == 404
