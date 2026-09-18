import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

from app.services.jobs import process


def _spawn_sleeper() -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _wait_until_gone(pid: int, timeout: float = 10.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not process.is_running(pid):
            return True
        time.sleep(0.1)
    return False


def test_a_live_process_reads_as_running():
    child = _spawn_sleeper()
    try:
        assert process.is_running(child.pid) is True
    finally:
        child.kill()
        child.wait(timeout=10)


def test_a_finished_process_does_not_read_as_running():
    child = _spawn_sleeper()
    child.kill()
    child.wait(timeout=10)

    assert _wait_until_gone(child.pid)


def test_terminate_stops_a_running_process():
    child = _spawn_sleeper()
    try:
        process.terminate(child.pid)
        assert _wait_until_gone(child.pid)
    finally:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=10)


def test_terminating_something_already_gone_is_not_an_error():
    """Cancel races the job finishing on its own. Losing that race must be
    a no-op, not a crash."""
    child = _spawn_sleeper()
    child.kill()
    child.wait(timeout=10)
    _wait_until_gone(child.pid)

    process.terminate(child.pid)  # must not raise


def test_no_pid_is_not_running():
    assert process.is_running(None) is False


def test_a_pid_that_predates_the_job_is_treated_as_a_stranger():
    """PIDs get recycled. After a restart, a job's stored pid may belong to
    an unrelated process - and killing that would be someone else's very
    bad day. A process that started before the job did cannot be its
    worker."""
    child = _spawn_sleeper()
    try:
        the_future = datetime.now(timezone.utc) + timedelta(hours=1)
        assert process.is_running(child.pid, started_after=the_future) is False
        # ...and without the guard it is plainly alive.
        assert process.is_running(child.pid) is True
    finally:
        child.kill()
        child.wait(timeout=10)


def test_a_pid_that_postdates_the_job_is_accepted_as_its_worker():
    child = _spawn_sleeper()
    try:
        well_before = datetime.now(timezone.utc) - timedelta(hours=1)
        assert process.is_running(child.pid, started_after=well_before) is True
    finally:
        child.kill()
        child.wait(timeout=10)


def test_creation_time_is_reported_for_a_live_process():
    """If this returns None the reuse guard silently stops working, so it
    is worth asserting the platform actually supports it."""
    child = _spawn_sleeper()
    try:
        created = process.creation_time(child.pid)
        assert created is not None
        assert created <= datetime.now(timezone.utc) + timedelta(seconds=5)
    finally:
        child.kill()
        child.wait(timeout=10)
