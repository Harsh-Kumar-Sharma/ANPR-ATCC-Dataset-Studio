"""A live capture that the app outlived.

Capture sessions are threads inside the backend process, so closing
the app ends them - but the processing_run row went on saying
"running". Nothing could settle it either: Stop looks the session up
in an in-memory registry that restarted empty, and answered 404.

What the user actually hit: a source that could not be deleted, with a
message telling them to stop a capture that no longer existed.
"""

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.db.models.processing_run import ProcessingRun
from app.db.models.source import Source
from app.db.session import SessionLocal
from app.main import app
from app.services.live_reconcile import reconcile_live_captures, unfinished_live_runs
from app.services.rtsp_session_registry import register_session, remove_session

client = TestClient(app)


def _project(name: str) -> dict:
    return client.post("/projects", json={"name": name}).json()


def _abandoned_live_run(project: dict, status: str = "running") -> tuple[str, str]:
    """A source and a live run left as a closing app would leave them."""
    with SessionLocal() as db:
        source = Source(
            project_id=project["id"],
            type="rtsp",
            path_or_uri="rtsp://admin:secret@10.0.0.4:9001/Streaming/channels/1",
            fps=10.0,
            width=0,
            height=0,
            duration_ms=0,
            frame_count=0,
        )
        db.add(source)
        db.flush()
        run = ProcessingRun(
            source_id=source.id,
            sampling_config={"protocol": "rtsp", "expected_fps": 10.0, "buffer_maxlen": 300},
            status=status,
            started_at=datetime(2026, 9, 19, 11, 55, tzinfo=timezone.utc),
        )
        db.add(run)
        db.commit()
        return source.id, run.id


# --- settling them ------------------------------------------------------------


def test_a_live_run_with_no_session_behind_it_is_settled():
    """It cannot be running: the threads went with the process."""
    project = _project("Abandoned Capture")
    _, run_id = _abandoned_live_run(project)

    with SessionLocal() as db:
        settled = reconcile_live_captures(db)

    assert run_id in settled
    run = client.get(f"/processing-runs/{run_id}").json()
    assert run["status"] == "failed"
    assert run["completed_at"] is not None


def test_the_settled_run_says_what_happened_to_it():
    """"failed" on its own sends the reader looking for a fault in the
    camera."""
    project = _project("Abandoned Capture Message")
    _, run_id = _abandoned_live_run(project)

    with SessionLocal() as db:
        reconcile_live_captures(db)

    message = client.get(f"/processing-runs/{run_id}").json()["error_message"]
    assert "closed" in message.lower()
    assert "kept" in message.lower()


def test_a_capture_that_is_actually_running_is_left_alone():
    """The same function runs at startup and could run later; settling
    a live session out from under itself would be worse than the bug
    it fixes."""
    project = _project("Live And Well")
    _, run_id = _abandoned_live_run(project)
    register_session(run_id, object())  # type: ignore[arg-type]
    try:
        with SessionLocal() as db:
            settled = reconcile_live_captures(db)
    finally:
        remove_session(run_id)

    assert run_id not in settled
    assert client.get(f"/processing-runs/{run_id}").json()["status"] == "running"


def test_an_offline_run_is_not_touched(tmp_path):
    """A detect job is a detached process that may well still be
    running. Reattaching to those is a different mechanism."""
    project = _project("Offline Run Untouched")
    with SessionLocal() as db:
        source = Source(
            project_id=project["id"], type="video", path_or_uri="clip.mp4", fps=25.0,
            width=1920, height=1080, duration_ms=1000, frame_count=100,
        )
        db.add(source)
        db.flush()
        run = ProcessingRun(source_id=source.id, sampling_config={"target_fps": 5.0}, status="running")
        db.add(run)
        db.commit()
        run_id = run.id

        assert run_id not in [r.id for r in unfinished_live_runs(db)]
        assert run_id not in reconcile_live_captures(db)


# --- stopping one by hand -----------------------------------------------------


def test_stopping_a_capture_the_registry_forgot_settles_it_instead_of_refusing():
    """The user pressing Stop is right. Answering 404 left them with
    no way to act on it."""
    project = _project("Stop The Forgotten")
    _, run_id = _abandoned_live_run(project)

    response = client.post(f"/processing-runs/{run_id}/rtsp/stop")

    assert response.status_code == 200, response.text
    assert response.json()["stopped"] is True
    assert client.get(f"/processing-runs/{run_id}").json()["status"] == "failed"


def test_stopping_a_run_that_never_existed_is_still_a_404():
    response = client.post("/processing-runs/not-a-run/rtsp/stop")

    assert response.status_code == 404


def test_stopping_an_already_settled_run_does_not_rewrite_its_ending():
    project = _project("Stop Twice")
    _, run_id = _abandoned_live_run(project)
    client.post(f"/processing-runs/{run_id}/rtsp/stop")
    first = client.get(f"/processing-runs/{run_id}").json()

    client.post(f"/processing-runs/{run_id}/rtsp/stop")

    assert client.get(f"/processing-runs/{run_id}").json()["completed_at"] == first["completed_at"]


# --- and the thing the user was actually trying to do -------------------------


def test_the_source_can_be_deleted_once_its_abandoned_capture_is_settled():
    """The whole point. Two of these blocked a real source
    permanently, with a message telling the user to stop a capture
    that no longer existed."""
    project = _project("Delete After Settling")
    source_id, _ = _abandoned_live_run(project)

    blocked = client.delete(f"/projects/{project['id']}/sources/{source_id}")
    assert blocked.status_code == 409, "an unfinished capture should block, when it really is unfinished"

    with SessionLocal() as db:
        reconcile_live_captures(db)

    removed = client.delete(f"/projects/{project['id']}/sources/{source_id}")
    assert removed.status_code == 200, removed.text


def test_the_contents_report_stops_claiming_a_running_capture_after_settling():
    """The confirmation panel reads this, so it has to agree with the
    delete it is confirming."""
    project = _project("Contents Agree")
    source_id, _ = _abandoned_live_run(project)

    with SessionLocal() as db:
        reconcile_live_captures(db)

    contents = client.get(f"/projects/{project['id']}/sources/{source_id}/contents").json()
    assert contents["running_jobs"] == 0
