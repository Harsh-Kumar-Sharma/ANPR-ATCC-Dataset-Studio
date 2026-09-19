"""Clearing finished jobs out of the list.

A job row outlives the work by design - that is how a failure stays
readable after the fact. But nothing ever removed one, so the panel
accumulates every run the project has ever made. The real project holds
five dead jobs, three of them failures from a bug that has since been
fixed.

Two files hang off each job that nothing has ever cleaned up either:
its progress file and its worker log, both outside the workspace.
"""

from fastapi.testclient import TestClient

from app.db.models import Job
from app.db.session import SessionLocal
from app.main import app
from app.services.jobs import runner

client = TestClient(app)


def _project(name: str) -> dict:
    return client.post("/projects", json={"name": name}).json()


def _job(project: dict, status: str, *, type: str = "detect") -> str:
    """A job row in a chosen state, with the two files a real one leaves."""
    with SessionLocal() as db:
        job = Job(project_id=project["id"], type=type, status=status, params_json={})
        db.add(job)
        db.commit()
        job_id = job.id

    runner.progress_path(job_id).parent.mkdir(parents=True, exist_ok=True)
    runner.progress_path(job_id).write_text('{"fraction": 1.0}', encoding="utf-8")
    runner.log_path(job_id).write_text("worker output", encoding="utf-8")
    return job_id


def _jobs(project: dict) -> list[dict]:
    return client.get("/jobs", params={"project_id": project["id"]}).json()


# --- one at a time ------------------------------------------------------------


def test_a_failed_job_can_be_dismissed():
    project = _project("Dismiss Failed")
    job_id = _job(project, "failed")

    response = client.delete(f"/jobs/{job_id}")

    assert response.status_code == 200, response.text
    assert job_id not in [j["id"] for j in _jobs(project)]


def test_a_cancelled_job_can_be_dismissed():
    project = _project("Dismiss Cancelled")
    job_id = _job(project, "cancelled")

    assert client.delete(f"/jobs/{job_id}").status_code == 200
    assert _jobs(project) == []


def test_a_succeeded_job_can_be_dismissed():
    project = _project("Dismiss Succeeded")
    job_id = _job(project, "succeeded")

    assert client.delete(f"/jobs/{job_id}").status_code == 200


def test_dismissing_takes_the_progress_file_and_the_log_with_it():
    """Both live outside the workspace, so a project delete never
    reached them either. Every job of every deleted project has been
    leaving two files behind forever."""
    project = _project("Dismiss Files")
    job_id = _job(project, "failed")
    assert runner.progress_path(job_id).is_file()
    assert runner.log_path(job_id).is_file()

    client.delete(f"/jobs/{job_id}")

    assert not runner.progress_path(job_id).exists()
    assert not runner.log_path(job_id).exists()


def test_a_running_job_is_not_dismissed():
    """Its worker is still writing to that row and those files. Cancel
    it first - that is what cancel is for."""
    project = _project("Dismiss Running")
    job_id = _job(project, "running")

    response = client.delete(f"/jobs/{job_id}")

    assert response.status_code == 409
    assert response.json()["code"] == "job_running"
    assert "cancel" in response.json()["message"].lower()
    assert client.get(f"/jobs/{job_id}").status_code == 200


def test_a_pending_job_is_not_dismissed():
    project = _project("Dismiss Pending")
    job_id = _job(project, "pending")

    assert client.delete(f"/jobs/{job_id}").status_code == 409


def test_dismissing_an_unknown_job_is_a_404():
    assert client.delete("/jobs/does-not-exist").status_code == 404


# --- all at once --------------------------------------------------------------


def test_clearing_finished_removes_every_finished_job_of_a_project():
    project = _project("Clear Finished")
    finished = [_job(project, s) for s in ("failed", "cancelled", "succeeded")]
    running = _job(project, "running")

    response = client.post("/jobs/clear-finished", json={"project_id": project["id"]})

    assert response.status_code == 200, response.text
    assert response.json()["removed"] == 3
    assert [j["id"] for j in _jobs(project)] == [running]
    for job_id in finished:
        assert not runner.log_path(job_id).exists()


def test_clearing_finished_leaves_another_projects_jobs_alone():
    mine = _project("Clear Mine")
    theirs = _project("Clear Theirs")
    _job(mine, "failed")
    survivor = _job(theirs, "failed")

    client.post("/jobs/clear-finished", json={"project_id": mine["id"]})

    assert [j["id"] for j in _jobs(theirs)] == [survivor]


def test_clearing_finished_with_nothing_to_clear_says_zero():
    project = _project("Clear Nothing")
    _job(project, "running")

    response = client.post("/jobs/clear-finished", json={"project_id": project["id"]})

    assert response.status_code == 200
    assert response.json()["removed"] == 0
