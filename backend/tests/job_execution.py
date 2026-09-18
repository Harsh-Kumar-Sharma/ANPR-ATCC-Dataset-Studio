"""Running a submitted job inline, for tests.

Detection used to happen inside the request, so a test could post to
``/process`` and read the tracks out of the response. It now runs in a
detached process that shares no memory with the test - it cannot see a
stubbed detector, and the response returns before any work is done.

Rather than assert against a mocked-out job system, these helpers keep
the real path: submit through the API exactly as the app does, then run
that job's actual handler in-process with the stub wired in. What is
skipped is only the process boundary.
"""

from contextlib import contextmanager

from app.main import app
from app.services.jobs import handlers, runner, worker


class _InlineLauncher:
    """Records the job instead of spawning a process, so the caller can
    decide when (and whether) to run it."""

    def __init__(self) -> None:
        self.job_ids: list[str] = []

    def __call__(self, job_id: str) -> int:
        self.job_ids.append(job_id)
        return -1  # not a real pid; nothing in a test should signal it


@contextmanager
def deferred_jobs():
    """Submit jobs without running them. Yields the recording launcher."""
    launcher = _InlineLauncher()
    app.dependency_overrides[runner.get_launcher] = lambda: launcher
    try:
        yield launcher
    finally:
        app.dependency_overrides.pop(runner.get_launcher, None)


@contextmanager
def run_jobs_inline(detector=None, monkeypatch=None):
    """Run every job submitted inside the block, as it is submitted.

    ``detector`` replaces the one the detect handler would build, which
    is the whole reason this helper exists: model loading is slow and
    non-deterministic, and the process boundary hides it from the usual
    FastAPI dependency override.
    """
    original = handlers.get_default_detector
    if detector is not None:
        handlers.get_default_detector = lambda: detector

    launcher = _InlineLauncher()

    def run_immediately(job_id: str) -> int:
        launcher(job_id)
        worker.run_job(job_id)
        return -1

    app.dependency_overrides[runner.get_launcher] = lambda: run_immediately
    try:
        yield launcher
    finally:
        app.dependency_overrides.pop(runner.get_launcher, None)
        handlers.get_default_detector = original


def process_source_sync(client, project_id: str, source_id: str, detector, target_fps: float = 5.0) -> dict:
    """Post to ``/process`` and return once the work has actually happened.

    Returns the submission body (``job`` and ``run_id``). Tracks are read
    back through the tracks API, as any real client would.
    """
    with run_jobs_inline(detector=detector):
        response = client.post(
            f"/projects/{project_id}/sources/{source_id}/process",
            json={"sampling_config": {"target_fps": target_fps}},
        )
    response.raise_for_status()
    return response.json()


def tracks_for_run(client, project_id: str, run_id: str) -> list[dict]:
    return client.get(f"/projects/{project_id}/tracks", params={"run_id": run_id}).json()
