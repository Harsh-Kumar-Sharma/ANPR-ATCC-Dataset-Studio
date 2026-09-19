import pytest

import json

from app.services.jobs import progress as progress_module
from app.services.jobs.progress import read_progress, write_progress


def test_progress_round_trips(tmp_path):
    path = tmp_path / "job.progress.json"

    write_progress(path, fraction=0.42, message="frame 420 of 1000")
    progress = read_progress(path)

    assert progress is not None
    assert progress.fraction == 0.42
    assert progress.message == "frame 420 of 1000"


def test_progress_is_absent_before_the_worker_writes_anything(tmp_path):
    """A job that has been launched but has not yet reported is normal, not
    an error - the API must render it rather than fall over."""
    assert read_progress(tmp_path / "never-written.json") is None


def test_a_torn_or_corrupt_progress_file_reads_as_absent(tmp_path):
    """The reader races a live writer in another process. A half-written
    file must degrade to 'no progress yet', never take the API down."""
    path = tmp_path / "job.progress.json"
    path.write_text('{"fraction": 0.5, "mess', encoding="utf-8")

    assert read_progress(path) is None


def test_fraction_is_clamped_to_a_sane_range(tmp_path):
    """Progress drives a progress bar. A worker that miscounts its total
    must not produce a bar that runs off the end or backwards."""
    path = tmp_path / "job.progress.json"

    write_progress(path, fraction=1.7, message=None)
    assert read_progress(path).fraction == 1.0

    write_progress(path, fraction=-0.3, message=None)
    assert read_progress(path).fraction == 0.0


def test_writing_progress_replaces_rather_than_appends(tmp_path):
    path = tmp_path / "job.progress.json"

    write_progress(path, fraction=0.1, message="early")
    write_progress(path, fraction=0.9, message="late")

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["fraction"] == 0.9
    assert payload["message"] == "late"


def test_progress_carries_a_timestamp_so_a_stalled_job_is_detectable(tmp_path):
    path = tmp_path / "job.progress.json"

    write_progress(path, fraction=0.5, message=None)

    assert read_progress(path).updated_at is not None


def test_writing_creates_the_directory_it_needs(tmp_path):
    """The worker starts in its own process and must not assume the app
    already made its progress directory."""
    path = tmp_path / "nested" / "dir" / "job.progress.json"

    write_progress(path, fraction=0.0, message="starting")

    assert read_progress(path) is not None


def test_a_reader_mid_read_does_not_break_the_write(tmp_path):
    """The bug a real detection run hit.

    On Windows a file another process has open cannot be replaced -
    Python's `open` does not ask for delete sharing - and the app polls
    this file while the worker writes it. The reader's grip lasts one
    small read, so the collision is a race rather than a standoff, but
    over a three-minute run with thousands of writes it is routine. It
    used to reach the worker as a PermissionError that killed the run.
    """
    import threading

    path = tmp_path / "progress.json"
    write_progress(path, fraction=0.1, message="first")

    reading = threading.Event()
    done = threading.Event()

    def poll_like_the_app():
        with open(path, encoding="utf-8") as reader:
            reader.read()
            reading.set()
            # Held for longer than a real read, so the write is
            # guaranteed to collide rather than merely likely to.
            done.wait(0.05)

    poller = threading.Thread(target=poll_like_the_app)
    poller.start()
    reading.wait(1.0)

    write_progress(path, fraction=0.2, message="second")

    done.set()
    poller.join(1.0)
    assert read_progress(path).message == "second"


def test_the_replace_is_retried_rather_than_given_up_on(tmp_path, monkeypatch):
    """A reader's grip lasts microseconds, so waiting briefly is the
    whole fix. Deterministic here rather than relying on a race."""
    import os as os_module

    path = tmp_path / "progress.json"
    real_replace = os_module.replace
    attempts = {"count": 0}

    def flaky_replace(src, dst):
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise PermissionError(5, "Access is denied")
        return real_replace(src, dst)

    monkeypatch.setattr(progress_module.os, "replace", flaky_replace)

    write_progress(path, fraction=0.5, message="through the retries")

    assert attempts["count"] == 3
    assert read_progress(path).message == "through the retries"


def test_a_replace_that_never_succeeds_still_raises(tmp_path, monkeypatch):
    """Retrying is not swallowing. A file that genuinely cannot be
    written should say so - the caller decides whether that matters."""
    path = tmp_path / "progress.json"

    def always_denied(src, dst):
        raise PermissionError(5, "Access is denied")

    monkeypatch.setattr(progress_module.os, "replace", always_denied)

    with pytest.raises(PermissionError):
        write_progress(path, fraction=0.5, message="never lands")

    assert list(tmp_path.glob("*.tmp")) == [], "the temp file goes even when the replace never works"
