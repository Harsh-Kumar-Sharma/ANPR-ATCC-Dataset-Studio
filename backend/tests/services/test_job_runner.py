import pytest

from app.db.models import Job, Project
from app.db.session import SessionLocal
from app.services.jobs import runner


@pytest.fixture
def project():
    with SessionLocal() as db:
        project = Project(name="Jobs Test Site", workspace_path="/workspace/jobs-test")
        db.add(project)
        db.commit()
        db.refresh(project)
        yield project


class RecordingLauncher:
    """Stands in for the detached subprocess so the lifecycle is testable
    without spawning one."""

    def __init__(self, pid: int = 4242, fail_with: Exception | None = None):
        self.pid = pid
        self.fail_with = fail_with
        self.launched: list[str] = []

    def __call__(self, job_id: str) -> int:
        if self.fail_with is not None:
            raise self.fail_with
        self.launched.append(job_id)
        return self.pid


def test_submitting_a_job_persists_it_and_launches_a_worker(project):
    launcher = RecordingLauncher()

    with SessionLocal() as db:
        job = runner.submit_job(
            db, type="detect", project_id=project.id, params={"source_id": "abc"}, launcher=launcher
        )

        assert job.id is not None
        assert job.type == "detect"
        assert job.params_json == {"source_id": "abc"}
        assert launcher.launched == [job.id]


def test_a_submitted_job_records_the_pid_so_it_can_be_found_again(project):
    """Without the pid the app cannot cancel the job, nor recognise it
    after a restart."""
    launcher = RecordingLauncher(pid=9001)

    with SessionLocal() as db:
        job = runner.submit_job(db, type="detect", project_id=project.id, params={}, launcher=launcher)

    with SessionLocal() as db:
        assert db.get(Job, job.id).pid == 9001


def test_a_job_is_durable_before_the_worker_is_launched(project):
    """The row must be committed first: a worker that starts fast enough to
    look for its own row must find one."""
    seen: dict[str, object] = {}

    def launcher(job_id: str) -> int:
        with SessionLocal() as other_connection:
            seen["visible"] = other_connection.get(Job, job_id) is not None
        return 1234

    with SessionLocal() as db:
        runner.submit_job(db, type="detect", project_id=project.id, params={}, launcher=launcher)

    assert seen["visible"] is True


def test_a_job_that_cannot_be_launched_is_failed_rather_than_left_pending(project):
    """A job stuck in pending forever is indistinguishable from a slow one.
    If the process never started, say so."""
    launcher = RecordingLauncher(fail_with=OSError("no such executable"))

    with SessionLocal() as db:
        with pytest.raises(OSError):
            runner.submit_job(db, type="detect", project_id=project.id, params={}, launcher=launcher)

    with SessionLocal() as db:
        failed = db.query(Job).order_by(Job.created_at.desc()).first()
        assert failed.status == "failed"
        assert failed.error_message is not None


def test_an_unknown_job_type_is_rejected(project):
    with SessionLocal() as db:
        with pytest.raises(ValueError):
            runner.submit_job(db, type="teleport", project_id=project.id, params={}, launcher=RecordingLauncher())


def test_a_worker_marks_its_job_running_and_then_succeeded(project):
    launcher = RecordingLauncher()
    with SessionLocal() as db:
        job = runner.submit_job(db, type="detect", project_id=project.id, params={}, launcher=launcher)

    with SessionLocal() as db:
        runner.mark_running(db, job.id)
        assert db.get(Job, job.id).status == "running"
        assert db.get(Job, job.id).started_at is not None

    with SessionLocal() as db:
        runner.mark_succeeded(db, job.id, result={"run_id": "r1"})
        finished = db.get(Job, job.id)
        assert finished.status == "succeeded"
        assert finished.result_json == {"run_id": "r1"}
        assert finished.completed_at is not None
        assert finished.progress == 1.0


def test_a_failed_job_keeps_its_error_where_the_user_can_see_it(project):
    launcher = RecordingLauncher()
    with SessionLocal() as db:
        job = runner.submit_job(db, type="detect", project_id=project.id, params={}, launcher=launcher)

    with SessionLocal() as db:
        runner.mark_failed(db, job.id, error="could not decode frame 12")

    with SessionLocal() as db:
        failed = db.get(Job, job.id)
        assert failed.status == "failed"
        assert "could not decode frame 12" in failed.error_message
        assert failed.completed_at is not None


def test_a_very_long_error_is_truncated_rather_than_breaking_the_write(project):
    """Tracebacks are long and the column is not. Losing the tail of an
    error beats losing the whole job record."""
    launcher = RecordingLauncher()
    with SessionLocal() as db:
        job = runner.submit_job(db, type="detect", project_id=project.id, params={}, launcher=launcher)

    with SessionLocal() as db:
        runner.mark_failed(db, job.id, error="x" * 10_000)

    with SessionLocal() as db:
        assert len(db.get(Job, job.id).error_message) <= 2048


def test_progress_for_a_finished_job_comes_from_the_row_not_the_file(project, tmp_path):
    """Progress files are transient; a job finished last week must still
    render sensibly."""
    launcher = RecordingLauncher()
    with SessionLocal() as db:
        job = runner.submit_job(db, type="detect", project_id=project.id, params={}, launcher=launcher)
        runner.mark_succeeded(db, job.id, result={})

    with SessionLocal() as db:
        fraction, message = runner.current_progress(db.get(Job, job.id))

    assert fraction == 1.0
