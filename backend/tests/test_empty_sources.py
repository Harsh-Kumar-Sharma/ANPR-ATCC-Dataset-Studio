"""Live sources that caught nothing, cleared away.

Every live capture start creates a source. A session that detected
nothing and kept no frames leaves that source behind holding
absolutely nothing - and after a few attempts at getting a camera
working, the Sources list is five identical rows reading "0 frames"
with the one that matters somewhere among them.
"""

from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.db.models.frame import Frame
from app.db.models.processing_run import ProcessingRun
from app.db.models.source import Source
from app.db.session import SessionLocal
from app.main import app
from app.ml.types import Detection
from app.services import empty_sources
from tests.job_execution import process_source_sync
from tests.video_factory import create_synthetic_video

client = TestClient(app)

WIDTH, HEIGHT = 128, 96


class _OneCarDetector:
    model_version = "one-car-stub-v1"
    class_names = {0: "car"}

    def detect(self, frame):
        return [Detection(bbox_xyxy=(20.0, 20.0, 80.0, 70.0), class_id=0, confidence=0.9)]


def _project(name: str) -> dict:
    return client.post("/projects", json={"name": name}).json()


def _live_source(project: dict, *, status: str = "completed", frames: int = 0) -> str:
    """A source as a finished live session leaves it."""
    return _live_source_and_run(project, status=status, frames=frames)[0]


def _live_source_and_run(project: dict, *, status: str = "completed", frames: int = 0) -> tuple[str, str]:
    """The same, when the caller needs the run as well."""
    with SessionLocal() as db:
        source = Source(
            project_id=project["id"], type="rtsp", path_or_uri="rtsp://camera/ch1",
            fps=10.0, width=0, height=0, duration_ms=0, frame_count=0,
        )
        db.add(source)
        db.flush()
        run = ProcessingRun(source_id=source.id, sampling_config={"protocol": "rtsp"}, status=status)
        db.add(run)
        for index in range(frames):
            db.add(Frame(source_id=source.id, frame_index=index, timestamp_ms=index * 100,
                         width=WIDTH, height=HEIGHT, image_path=f"/tmp/frame{index}.jpg"))
        db.commit()
        return source.id, run.id


def _sources(project: dict) -> list[str]:
    return [s["id"] for s in client.get(f"/projects/{project['id']}/sources").json()]


# --- what goes ----------------------------------------------------------------


def test_a_live_source_that_captured_nothing_is_removed():
    project = _project("Empty Capture")
    empty = _live_source(project)

    with SessionLocal() as db:
        removed = empty_sources.remove_empty(db, project["id"])

    assert removed == [empty]
    assert _sources(project) == []


def test_several_failed_attempts_are_all_cleared():
    """Five identical rows with the one that matters among them is
    the complaint this answers."""
    project = _project("Five Attempts")
    for _ in range(5):
        _live_source(project)
    kept = _live_source(project, frames=3)

    with SessionLocal() as db:
        empty_sources.remove_empty(db, project["id"])

    assert _sources(project) == [kept]


# --- what stays ---------------------------------------------------------------


def test_a_live_source_with_frames_stays():
    project = _project("Has Frames")
    kept = _live_source(project, frames=2)

    with SessionLocal() as db:
        assert empty_sources.remove_empty(db, project["id"]) == []

    assert _sources(project) == [kept]


def test_a_capture_that_is_still_going_is_left_alone():
    """Removing the source out from under a running session would
    take its run with it."""
    project = _project("Still Capturing")
    live = _live_source(project, status="running")

    with SessionLocal() as db:
        assert empty_sources.remove_empty(db, project["id"]) == []

    assert _sources(project) == [live]


def test_a_video_that_has_not_been_processed_yet_is_never_touched(tmp_path):
    """It also has no frames. Deleting it out from under someone
    about to press Detect would be a far worse bug than this one.
    """
    project = _project("Freshly Imported Video")
    video = create_synthetic_video(tmp_path / "clip.mp4", frame_count=4, fps=10.0, width=WIDTH, height=HEIGHT)
    imported = client.post(f"/projects/{project['id']}/sources", json={"path": str(video)}).json()

    with SessionLocal() as db:
        assert empty_sources.remove_empty(db, project["id"]) == []

    assert _sources(project) == [imported["id"]]


def test_a_processed_video_with_frames_stays(tmp_path):
    project = _project("Processed Video")
    video = create_synthetic_video(tmp_path / "clip.mp4", frame_count=4, fps=10.0, width=WIDTH, height=HEIGHT)
    source = client.post(f"/projects/{project['id']}/sources", json={"path": str(video)}).json()
    process_source_sync(client, project["id"], source["id"], _OneCarDetector(), target_fps=10.0)

    with SessionLocal() as db:
        empty_sources.remove_empty(db, project["id"])

    assert _sources(project) == [source["id"]]


def test_another_project_is_not_swept_by_accident():
    project = _project("Mine")
    other = _project("Theirs")
    theirs = _live_source(other)

    with SessionLocal() as db:
        empty_sources.remove_empty(db, project["id"])

    assert _sources(other) == [theirs]


# --- through the app ----------------------------------------------------------


def test_stopping_a_session_that_caught_nothing_clears_its_source():
    """The moment it becomes rubbish is the moment it should go."""
    project = _project("Tidied On Stop")
    _, run_id = _live_source_and_run(project, status="running")

    stopped = client.post(f"/processing-runs/{run_id}/rtsp/stop")

    assert stopped.status_code == 200, stopped.text

    assert _sources(project) == []


def test_a_source_holding_labels_is_never_swept(tmp_path):
    """The one thing that must never be lost to tidying."""
    project = _project("Has Labels")
    source_id = _live_source(project, frames=1)
    frame = client.get(f"/projects/{project['id']}/frames", params={"source_id": source_id}).json()[0]
    client.put(
        f"/frames/{frame['id']}/annotations",
        json={"annotations": [{"class_id": 4, "bbox_json": [1.0, 1.0, 9.0, 9.0]}]},
    )

    with SessionLocal() as db:
        empty_sources.remove_empty(db, project["id"])

    assert source_id in _sources(project)
