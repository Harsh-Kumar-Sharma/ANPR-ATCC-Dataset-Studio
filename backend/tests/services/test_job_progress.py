import json

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
