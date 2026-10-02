import os
import subprocess
import sys

import pytest

from app.core.errors import NotFoundError
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


def test_cancelling_a_job_stops_its_process_and_marks_it_cancelled(project):
    killed: list[int] = []
    launcher = RecordingLauncher(pid=7777)
    with SessionLocal() as db:
        job = runner.submit_job(db, type="detect", project_id=project.id, params={}, launcher=launcher)

    with SessionLocal() as db:
        runner.mark_running(db, job.id)
        runner.cancel_job(db, job.id, terminate=killed.append)

    with SessionLocal() as db:
        cancelled = db.get(Job, job.id)
        assert cancelled.status == "cancelled"
        assert cancelled.completed_at is not None
    assert killed == [7777], "the worker process must actually be stopped, not just relabelled"


def test_cancelling_a_finished_job_does_not_rewrite_its_outcome(project):
    """Cancel races the job finishing. Losing that race must leave the
    result alone rather than relabelling a completed run as cancelled."""
    killed: list[int] = []
    with SessionLocal() as db:
        job = runner.submit_job(db, type="detect", project_id=project.id, params={}, launcher=RecordingLauncher())
        runner.mark_succeeded(db, job.id, result={"run_id": "r1"})

    with SessionLocal() as db:
        runner.cancel_job(db, job.id, terminate=killed.append)

    with SessionLocal() as db:
        assert db.get(Job, job.id).status == "succeeded"
    assert killed == []


def test_cancelling_an_unknown_job_is_a_not_found(project):
    with SessionLocal() as db:
        with pytest.raises(NotFoundError):
            runner.cancel_job(db, "does-not-exist")


def test_reconcile_fails_a_job_whose_process_died_while_the_app_was_closed(project):
    """A job left 'running' by a crash or a kill would otherwise sit there
    claiming to be in progress forever."""
    with SessionLocal() as db:
        job = runner.submit_job(db, type="detect", project_id=project.id, params={}, launcher=RecordingLauncher())
        runner.mark_running(db, job.id)

    with SessionLocal() as db:
        reconciled = runner.reconcile_jobs(db, is_running=lambda pid, started_after=None: False)

    assert job.id in reconciled
    with SessionLocal() as db:
        dead = db.get(Job, job.id)
        assert dead.status == "failed"
        assert dead.completed_at is not None
        assert "no longer running" in dead.error_message


def test_reconcile_leaves_a_job_whose_process_is_still_going(project):
    """The whole point of detaching the worker: closing the app must not
    cost you the run."""
    with SessionLocal() as db:
        job = runner.submit_job(db, type="detect", project_id=project.id, params={}, launcher=RecordingLauncher())
        runner.mark_running(db, job.id)

    with SessionLocal() as db:
        reconciled = runner.reconcile_jobs(db, is_running=lambda pid, started_after=None: True)

    assert reconciled == [], "a job whose process is alive must be left alone"
    with SessionLocal() as db:
        assert db.get(Job, job.id).status == "running"


def test_reconcile_ignores_jobs_that_already_finished(project):
    with SessionLocal() as db:
        job = runner.submit_job(db, type="detect", project_id=project.id, params={}, launcher=RecordingLauncher())
        runner.mark_succeeded(db, job.id, result={})

    with SessionLocal() as db:
        reconciled = runner.reconcile_jobs(db, is_running=lambda pid, started_after=None: False)

    assert job.id not in reconciled
    with SessionLocal() as db:
        assert db.get(Job, job.id).status == "succeeded"


def test_reconcile_checks_the_pid_against_when_the_job_started(project):
    """Guards against pid reuse: the liveness check must be told when the
    job was created, or a recycled pid reads as a healthy worker."""
    seen: list = []

    def is_running(pid, started_after=None):
        seen.append((pid, started_after))
        return True

    with SessionLocal() as db:
        job = runner.submit_job(db, type="detect", project_id=project.id, params={}, launcher=RecordingLauncher())
        runner.mark_running(db, job.id)

    with SessionLocal() as db:
        runner.reconcile_jobs(db, is_running=is_running)

    assert seen and seen[0][1] is not None


def test_a_cancelled_job_cannot_be_reopened_by_its_dying_worker(project):
    """terminate() is asynchronous: taskkill and SIGTERM both return before
    the process is gone. So a worker can still be inside its own error
    handling and call mark_failed *after* the cancel has committed, which
    would relabel a deliberate cancellation as a crash."""
    with SessionLocal() as db:
        job = runner.submit_job(db, type="detect", project_id=project.id, params={}, launcher=RecordingLauncher())
        runner.mark_running(db, job.id)
        runner.cancel_job(db, job.id, terminate=lambda pid: None)

    # The worker, not yet dead, reports the interruption it just suffered.
    with SessionLocal() as db:
        runner.mark_failed(db, job.id, error="KeyboardInterrupt")

    with SessionLocal() as db:
        assert db.get(Job, job.id).status == "cancelled"


def test_a_cancelled_job_cannot_be_completed_by_its_dying_worker(project):
    """The same race in the other direction: a worker that finished just as
    the cancel landed must not resurrect the job as succeeded."""
    with SessionLocal() as db:
        job = runner.submit_job(db, type="detect", project_id=project.id, params={}, launcher=RecordingLauncher())
        runner.mark_running(db, job.id)
        runner.cancel_job(db, job.id, terminate=lambda pid: None)

    with SessionLocal() as db:
        runner.mark_succeeded(db, job.id, result={"run_id": "r1"})

    with SessionLocal() as db:
        assert db.get(Job, job.id).status == "cancelled"


def test_a_reconciled_job_and_its_run_tell_the_same_story(project):
    """A job marked failed whose run says 'cancelled' misreports what
    happened. Both must land on the same outcome."""
    from app.db.models.processing_run import ProcessingRun
    from app.db.models.source import Source

    with SessionLocal() as db:
        source = Source(
            project_id=project.id,
            type="video",
            path_or_uri="/nowhere.mp4",
            fps=10.0,
            width=64,
            height=48,
            frame_count=10,
            duration_ms=1000,
        )
        db.add(source)
        db.flush()
        run = ProcessingRun(source_id=source.id, sampling_config={"target_fps": 5.0}, status="running")
        db.add(run)
        db.flush()
        job = runner.submit_job(
            db, type="detect", project_id=project.id, params={"run_id": run.id}, launcher=RecordingLauncher()
        )
        runner.mark_running(db, job.id)
        run_id = run.id

    with SessionLocal() as db:
        runner.reconcile_jobs(db, is_running=lambda pid, started_after=None: False)

    with SessionLocal() as db:
        assert db.get(Job, job.id).status == "failed"
        assert db.get(ProcessingRun, run_id).status == "failed", "the run must not claim it was cancelled"


# --- a worker that dies while the app is running ------------------------------


def test_a_worker_killed_while_the_app_runs_fails_its_job_at_once(project):
    """The kernel's out-of-memory killer took a training run at epoch 13
    and the job said "running, 23%" for seven hours. Reconcile only runs
    when the app starts; a worker that dies under a running app has to
    be noticed when it dies."""
    with SessionLocal() as db:
        job = runner.submit_job(db, type="detect", project_id=project.id, params={}, launcher=RecordingLauncher())
        runner.mark_running(db, job.id)

    with SessionLocal() as db:
        runner.settle_abandoned(db, job.id, returncode=-9)

    with SessionLocal() as db:
        dead = db.get(Job, job.id)
        assert dead.status == "failed"
        assert dead.completed_at is not None
        assert "memory" in dead.error_message, "SIGKILL from the kernel almost always means out of memory - say so"


def test_a_worker_that_crashes_says_how_it_exited(project):
    with SessionLocal() as db:
        job = runner.submit_job(db, type="detect", project_id=project.id, params={}, launcher=RecordingLauncher())
        runner.mark_running(db, job.id)
        runner.settle_abandoned(db, job.id, returncode=3)

    with SessionLocal() as db:
        dead = db.get(Job, job.id)
        assert dead.status == "failed"
        assert "code 3" in dead.error_message
        assert str(runner.log_path(job.id)) in dead.error_message


def test_a_worker_that_settled_its_own_job_is_left_alone(project):
    """The normal end: the worker records the outcome, then exits."""
    with SessionLocal() as db:
        job = runner.submit_job(db, type="detect", project_id=project.id, params={}, launcher=RecordingLauncher())
        runner.mark_running(db, job.id)
        runner.mark_succeeded(db, job.id, {"ok": True})
        runner.settle_abandoned(db, job.id, returncode=0)

    with SessionLocal() as db:
        assert db.get(Job, job.id).status == "succeeded"


@pytest.mark.skipif(sys.platform == "win32", reason="zombies are a POSIX thing")
def test_a_dead_worker_is_reaped_and_reported():
    """Nothing in the app ever waited on its workers, so a dead one
    stayed behind as a zombie - and a zombie still answers to its pid,
    which is exactly what made the job look alive."""
    child = subprocess.Popen(
        [sys.executable, "-c", "import os, signal; os.kill(os.getpid(), signal.SIGKILL)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    exited: list[int] = []

    watcher = runner.watch_worker(child, exited.append)
    watcher.join(timeout=10)

    assert exited == [-9]
    with pytest.raises(ChildProcessError):
        os.waitpid(child.pid, os.WNOHANG)  # already reaped: nothing left to wait for
