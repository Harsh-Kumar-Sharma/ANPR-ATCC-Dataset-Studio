import pytest

from app.db.models import Job, Project
from app.db.session import SessionLocal
from app.services.jobs import runner, worker
from app.services.jobs.handlers import JobHandler
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
    monkeypatch.setitem(worker.HANDLERS, "detect", JobHandler(run=lambda db, params, report: {"run_id": "r-7"}))
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

    monkeypatch.setitem(worker.HANDLERS, "detect", JobHandler(run=handler))
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

    monkeypatch.setitem(worker.HANDLERS, "detect", JobHandler(run=handler))
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

    monkeypatch.setitem(worker.HANDLERS, "detect", JobHandler(run=handler))
    job = _submit(project, params={"run_id": "r-1", "target_fps": 5})
    worker.run_job(job.id)

    assert seen == {"run_id": "r-1", "target_fps": 5}


def test_a_job_that_was_already_cancelled_is_not_run(project, monkeypatch):
    """A cancel can land between launching the process and the process
    getting as far as looking at its row."""
    ran = []
    monkeypatch.setitem(worker.HANDLERS, "detect", JobHandler(run=lambda db, params, report: ran.append(1) or {}))
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


def test_selection_sets_aside_the_frames_it_passed_over(tmp_path, project, monkeypatch):
    """The handler end to end on a real video: decode, score, and leave
    every frame with a status and a reason."""
    from app.db.models import Frame, Source
    from app.services.jobs.handlers import run_select_job
    from tests.video_factory import create_synthetic_video

    video = create_synthetic_video(tmp_path / "select.mp4", frame_count=12, fps=10.0)
    with SessionLocal() as db:
        db.get(Project, project.id).workspace_path = str(tmp_path / "ws")
        source = Source(
            project_id=project.id,
            type="video",
            path_or_uri=str(video),
            fps=10.0,
            width=64,
            height=48,
            frame_count=12,
            duration_ms=1200,
        )
        db.add(source)
        db.flush()
        for index in range(6):
            db.add(Frame(source_id=source.id, frame_index=index, timestamp_ms=index * 100, width=64, height=48))
        db.commit()
        source_id = source.id

    with SessionLocal() as db:
        result = run_select_job(db, {"source_id": source_id}, lambda fraction, message=None: None)

    assert result["considered"] == 6
    with SessionLocal() as db:
        frames = db.query(Frame).filter(Frame.source_id == source_id).all()
        assert all(f.selection_reason for f in frames), "every frame says why it was kept or passed over"
        assert all(f.status in ("pending", "skipped") for f in frames)
        # A synthetic clip is the same scene throughout, so most of it is
        # near-duplicate - which is the whole point of selection.
        assert len([f for f in frames if f.status == "skipped"]) > 0


def test_selection_leaves_frames_a_human_already_touched_alone(tmp_path, project):
    """Selection suggests what to look at next; it does not overrule
    someone who has already looked."""
    from app.db.models import Frame, Source
    from app.services.jobs.handlers import run_select_job
    from tests.video_factory import create_synthetic_video

    video = create_synthetic_video(tmp_path / "keep.mp4", frame_count=6, fps=10.0)
    with SessionLocal() as db:
        db.get(Project, project.id).workspace_path = str(tmp_path / "ws2")
        source = Source(
            project_id=project.id,
            type="video",
            path_or_uri=str(video),
            fps=10.0,
            width=64,
            height=48,
            frame_count=6,
            duration_ms=600,
        )
        db.add(source)
        db.flush()
        labelled = Frame(
            source_id=source.id, frame_index=0, timestamp_ms=0, width=64, height=48, status="labeled"
        )
        rejected = Frame(
            source_id=source.id, frame_index=1, timestamp_ms=100, width=64, height=48, status="rejected"
        )
        db.add_all([labelled, rejected, Frame(source_id=source.id, frame_index=2, timestamp_ms=200, width=64, height=48)])
        db.commit()
        source_id, labelled_id, rejected_id = source.id, labelled.id, rejected.id

    with SessionLocal() as db:
        result = run_select_job(db, {"source_id": source_id}, lambda fraction, message=None: None)

    assert result["considered"] == 1
    with SessionLocal() as db:
        assert db.get(Frame, labelled_id).status == "labeled"
        assert db.get(Frame, rejected_id).status == "rejected"


def test_selection_does_not_cache_the_frames_it_reads(tmp_path, project):
    """Selection looks at every frame once and throws the pixels away.
    Caching them writes about a gigabyte of JPEGs per source for a
    computation that needs none of them - which on a nearly-full disk is
    the difference between a slow job and a failed one."""
    from app.db.models import Frame, Source
    from app.services.frame_materializer import frames_root
    from app.services.jobs.handlers import run_select_job
    from tests.video_factory import create_synthetic_video

    workspace = tmp_path / "ws-no-cache"
    video = create_synthetic_video(tmp_path / "nocache.mp4", frame_count=12, fps=10.0)
    with SessionLocal() as db:
        db.get(Project, project.id).workspace_path = str(workspace)
        source = Source(
            project_id=project.id,
            type="video",
            path_or_uri=str(video),
            fps=10.0,
            width=64,
            height=48,
            frame_count=12,
            duration_ms=1200,
        )
        db.add(source)
        db.flush()
        for index in range(8):
            db.add(Frame(source_id=source.id, frame_index=index, timestamp_ms=index * 100, width=64, height=48))
        db.commit()
        source_id = source.id

    with SessionLocal() as db:
        run_select_job(db, {"source_id": source_id}, lambda fraction, message=None: None)

    written = list(frames_root(workspace).glob("**/*.jpg")) if frames_root(workspace).exists() else []
    assert written == [], "selection must not leave decoded frames on disk"
    with SessionLocal() as db:
        assert all(f.image_path is None for f in db.query(Frame).filter(Frame.source_id == source_id))


def test_a_progress_write_that_fails_does_not_kill_the_job(project, monkeypatch):
    """A three-minute detection run died because a status file could not
    be replaced. Progress is how the work is described, not the work -
    it must never be able to destroy what it reports on."""
    def refuse_to_write(*args, **kwargs):
        raise PermissionError(5, "Access is denied")

    monkeypatch.setattr(worker, "write_progress", refuse_to_write)
    monkeypatch.setitem(
        worker.HANDLERS,
        "detect",
        JobHandler(run=lambda db, params, report: (report(0.5, "halfway"), {"run_id": "r-9"})[1]),
    )
    job = _submit(project)

    assert worker.run_job(job.id) == worker.EXIT_OK

    with SessionLocal() as db:
        finished = db.get(Job, job.id)
        assert finished.status == "succeeded"
        assert finished.result_json == {"run_id": "r-9"}


def test_a_progress_failure_is_reported_once_rather_than_every_tick(project, monkeypatch, caplog):
    """A worker that cannot write progress will fail on every tick. One
    line saying so is a diagnosis; four thousand is a log nobody reads."""
    def refuse_to_write(*args, **kwargs):
        raise PermissionError(5, "Access is denied")

    monkeypatch.setattr(worker, "write_progress", refuse_to_write)

    def tick_a_lot(db, params, report):
        for i in range(20):
            report(i / 20, f"tick {i}")
        return {}

    monkeypatch.setitem(worker.HANDLERS, "detect", JobHandler(run=tick_a_lot))
    job = _submit(project)

    with caplog.at_level("WARNING"):
        assert worker.run_job(job.id) == worker.EXIT_OK

    complaints = [r for r in caplog.records if "progress" in r.getMessage().lower()]
    assert len(complaints) == 1, f"expected one warning, got {len(complaints)}"
