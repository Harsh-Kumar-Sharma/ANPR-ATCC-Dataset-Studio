import pytest

from app.db.models import Job, Project
from app.db.session import SessionLocal
from app.services.jobs import runner, worker
from app.services.jobs.progress import read_progress


@pytest.fixture
def project():
    with SessionLocal() as db:
        project = Project(name="Worker Test Site", workspace_path="/workspace/worker-test")
        db.add(project)
        db.commit()
        db.refresh(project)
        yield project


def _submit(project, params=None):
    with SessionLocal() as db:
        return runner.submit_job(
            db, type="detect", project_id=project.id, params=params or {}, launcher=lambda job_id: 1
        )


def test_a_worker_runs_its_handler_and_records_the_result(project, monkeypatch):
    monkeypatch.setitem(worker.HANDLERS, "detect", lambda db, params, report: {"run_id": "r-7"})
    job = _submit(project)

    assert worker.run_job(job.id) == worker.EXIT_OK

    with SessionLocal() as db:
        finished = db.get(Job, job.id)
        assert finished.status == "succeeded"
        assert finished.result_json == {"run_id": "r-7"}


def test_a_worker_reports_progress_where_the_app_can_read_it(project, monkeypatch):
    def handler(db, params, report):
        report(0.5, "halfway")
        return {}

    monkeypatch.setitem(worker.HANDLERS, "detect", handler)
    job = _submit(project)
    worker.run_job(job.id)

    # The job finished, so the durable answer is the row - but the file
    # is what the app polls while it is still running.
    with SessionLocal() as db:
        assert db.get(Job, job.id).progress == 1.0
    assert read_progress(runner.progress_path(job.id)).fraction == 1.0


def test_a_handler_that_raises_fails_the_job_with_its_error(project, monkeypatch):
    def handler(db, params, report):
        raise ValueError("could not decode frame 12")

    monkeypatch.setitem(worker.HANDLERS, "detect", handler)
    job = _submit(project)

    assert worker.run_job(job.id) == worker.EXIT_FAILED

    with SessionLocal() as db:
        failed = db.get(Job, job.id)
        assert failed.status == "failed"
        assert "could not decode frame 12" in failed.error_message
        assert failed.completed_at is not None


def test_the_handler_receives_the_params_the_job_was_submitted_with(project, monkeypatch):
    """The worker shares no memory with the app - params are the only way
    it learns what to do."""
    seen = {}

    def handler(db, params, report):
        seen.update(params)
        return {}

    monkeypatch.setitem(worker.HANDLERS, "detect", handler)
    job = _submit(project, params={"run_id": "r-1", "target_fps": 5})
    worker.run_job(job.id)

    assert seen == {"run_id": "r-1", "target_fps": 5}


def test_a_job_that_was_already_cancelled_is_not_run(project, monkeypatch):
    """A cancel can land between launching the process and the process
    getting as far as looking at its row."""
    ran = []
    monkeypatch.setitem(worker.HANDLERS, "detect", lambda db, params, report: ran.append(1) or {})
    job = _submit(project)

    with SessionLocal() as db:
        db.get(Job, job.id).status = "cancelled"
        db.commit()

    assert worker.run_job(job.id) == worker.EXIT_OK
    assert ran == []


def test_an_unknown_job_id_is_reported_rather_than_crashing(project):
    assert worker.run_job("not-a-real-job") == worker.EXIT_BAD_INVOCATION


def test_a_job_type_with_no_handler_fails_the_job(project, monkeypatch):
    job = _submit(project)
    monkeypatch.delitem(worker.HANDLERS, "detect", raising=False)

    assert worker.run_job(job.id) == worker.EXIT_BAD_INVOCATION

    with SessionLocal() as db:
        assert db.get(Job, job.id).status == "failed"


def test_invoking_the_worker_without_a_job_id_is_a_usage_error():
    assert worker.main(["worker"]) == worker.EXIT_BAD_INVOCATION
