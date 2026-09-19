"""Keeping a live session's frames, and clearing out the ones nobody
wanted.

A real session captured 2,453 frames and persisted no tracks at all,
which left nothing to label. Keeping the frames themselves is what
makes a session useful when the model finds nothing - and sweeping
them afterwards is what keeps that affordable.
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
from app.services.rolling_buffer import BufferedFrame
from app.services.rtsp_session import KeepFrames, RtspCaptureSession
from tests.job_execution import process_source_sync
from tests.video_factory import create_synthetic_video

client = TestClient(app)

WIDTH, HEIGHT = 128, 96
CAR_CLASS = 4


class _FindsNothing:
    """The situation this feature exists for."""

    model_version = "finds-nothing-v1"
    class_names = {0: "car"}

    def detect(self, frame):
        return []


class _OneCarDetector:
    model_version = "one-car-stub-v1"
    class_names = {0: "car"}

    def detect(self, frame):
        return [Detection(bbox_xyxy=(20.0, 20.0, 80.0, 70.0), class_id=0, confidence=0.9)]


def _project(name: str) -> dict:
    return client.post("/projects", json={"name": name}).json()


def _live_source(project: dict) -> tuple[str, str]:
    with SessionLocal() as db:
        source = Source(
            project_id=project["id"], type="rtsp", path_or_uri="rtsp://camera/ch1",
            fps=10.0, width=0, height=0, duration_ms=0, frame_count=0,
        )
        db.add(source)
        db.flush()
        run = ProcessingRun(
            source_id=source.id, sampling_config={"protocol": "rtsp"}, status="running"
        )
        db.add(run)
        db.commit()
        return source.id, run.id


def _session(project: dict, source_id: str, run_id: str, keep: KeepFrames, tmp_path) -> RtspCaptureSession:
    return RtspCaptureSession(
        run_id=run_id,
        source_id=source_id,
        keep_frames=keep,
        adapter=None,  # nothing is started; frames are fed in by hand
        detector=_FindsNothing(),
        tracker=None,
        workspace_path=Path(project["workspace_path"]),
    )


def _feed(session: RtspCaptureSession, count: int) -> None:
    """Push frames through the part of the loop that keeps them."""
    image = np.full((HEIGHT, WIDTH, 3), 90, dtype=np.uint8)
    for index in range(count):
        session._maybe_keep_frame(BufferedFrame(frame_index=index, timestamp_ms=index * 100, payload=image))


# --- keeping the frames -------------------------------------------------------


def test_nothing_is_kept_unless_it_was_asked_for(tmp_path):
    """Every frame of a long session at 1080p is gigabytes, so this is
    off until someone chooses it."""
    project = _project("Keep Off By Default")
    source_id, run_id = _live_source(project)
    session = _session(project, source_id, run_id, KeepFrames(), tmp_path)

    _feed(session, 50)

    assert session.status().frames_saved == 0


def test_one_frame_in_every_ten_is_kept(tmp_path):
    """Consecutive frames of a camera mostly show the same thing."""
    project = _project("Keep Every Tenth")
    source_id, run_id = _live_source(project)
    session = _session(project, source_id, run_id, KeepFrames(enabled=True, every=10), tmp_path)

    _feed(session, 100)

    assert session.status().frames_saved == 10


def test_the_ceiling_holds_however_long_the_session_runs(tmp_path):
    project = _project("Keep Ceiling")
    source_id, run_id = _live_source(project)
    session = _session(
        project, source_id, run_id, KeepFrames(enabled=True, every=1, max_frames=25), tmp_path
    )

    _feed(session, 500)

    assert session.status().frames_saved == 25


def test_kept_frames_reach_the_labelling_queue(tmp_path):
    """The whole point: a session that detected nothing still leaves
    something to label."""
    project = _project("Kept Frames Are Labellable")
    source_id, run_id = _live_source(project)
    session = _session(project, source_id, run_id, KeepFrames(enabled=True, every=5), tmp_path)
    _feed(session, 20)

    with SessionLocal() as db:
        session._persist_saved_frames(db)
        db.commit()

    queued = client.get(f"/projects/{project['id']}/frames", params={"source_id": source_id}).json()
    assert len(queued) == 4
    assert all(f["source_id"] == source_id for f in queued)


def test_a_kept_frame_can_actually_be_opened(tmp_path):
    """A row with no readable image is a broken picture in the queue."""
    project = _project("Kept Frame Opens")
    source_id, run_id = _live_source(project)
    session = _session(project, source_id, run_id, KeepFrames(enabled=True, every=1), tmp_path)
    _feed(session, 2)
    with SessionLocal() as db:
        session._persist_saved_frames(db)
        db.commit()

    frame = client.get(f"/projects/{project['id']}/frames", params={"source_id": source_id}).json()[0]
    image = client.get(f"/frames/{frame['id']}/image")

    assert image.status_code == 200, image.text
    assert len(image.content) > 0


def test_kept_frames_live_where_the_source_cascade_will_find_them(tmp_path):
    """Otherwise removing the source leaves the images orphaned."""
    project = _project("Kept Frames Cascade")
    source_id, run_id = _live_source(project)
    session = _session(project, source_id, run_id, KeepFrames(enabled=True, every=1), tmp_path)

    _feed(session, 3)

    expected = Path(project["workspace_path"]) / "derived" / "frames" / source_id
    assert sorted(p.name for p in expected.glob("*.jpg")) == [
        "frame_000000.jpg",
        "frame_000001.jpg",
        "frame_000002.jpg",
    ]


# --- sweeping the rest --------------------------------------------------------


def _video_project_with_frames(tmp_path, name: str) -> tuple[dict, list[dict]]:
    project = _project(name)
    video = create_synthetic_video(tmp_path / "clip.mp4", frame_count=6, fps=10.0, width=WIDTH, height=HEIGHT)
    source = client.post(f"/projects/{project['id']}/sources", json={"path": str(video)}).json()
    process_source_sync(client, project["id"], source["id"], _OneCarDetector(), target_fps=10.0)
    frames = client.get(f"/projects/{project['id']}/frames").json()
    return project, frames


def _label(frame: dict) -> None:
    response = client.put(
        f"/frames/{frame['id']}/annotations",
        json={"annotations": [{"class_id": CAR_CLASS, "bbox_json": [10.0, 10.0, 70.0, 60.0]}]},
    )
    assert response.status_code == 200, response.text


def test_the_sweep_says_what_it_would_take_before_it_takes_it(tmp_path):
    project, frames = _video_project_with_frames(tmp_path, "Sweep Preview")
    _label(frames[0])
    _label(frames[1])

    preview = client.get(f"/projects/{project['id']}/frames/sweep").json()

    assert preview["kept"] == 2
    assert preview["deleted"] == len(frames) - 2


def test_a_preview_deletes_nothing(tmp_path):
    project, frames = _video_project_with_frames(tmp_path, "Preview Is Safe")
    client.get(f"/projects/{project['id']}/frames/sweep")

    assert len(client.get(f"/projects/{project['id']}/frames").json()) == len(frames)


def test_the_sweep_keeps_what_was_labelled_and_deletes_the_rest(tmp_path):
    project, frames = _video_project_with_frames(tmp_path, "Sweep Runs")
    _label(frames[0])

    done = client.post(f"/projects/{project['id']}/frames/sweep").json()

    assert done["kept"] == 1
    assert done["deleted"] == len(frames) - 1
    remaining = client.get(f"/projects/{project['id']}/frames").json()
    assert [f["id"] for f in remaining] == [frames[0]["id"]]


def test_the_sweep_removes_the_images_too(tmp_path):
    """The images are the expensive part - a row is four hundred
    bytes, a 1080p JPEG is a quarter of a megabyte."""
    project, frames = _video_project_with_frames(tmp_path, "Sweep Frees Disk")
    # Opening a frame is what decodes it to disk.
    for frame in frames:
        client.get(f"/frames/{frame['id']}/image")
    _label(frames[0])

    done = client.post(f"/projects/{project['id']}/frames/sweep").json()

    assert done["bytes_freed"] > 0
    directory = Path(project["workspace_path"]) / "derived" / "frames"
    assert len(list(directory.rglob("*.jpg"))) == 1, "only the labelled frame's image survives"


def test_the_sweep_can_be_narrowed_to_one_source(tmp_path):
    project, frames = _video_project_with_frames(tmp_path, "Sweep One Source")
    other = create_synthetic_video(tmp_path / "b.mp4", frame_count=4, fps=10.0, width=WIDTH, height=HEIGHT)
    second = client.post(f"/projects/{project['id']}/sources", json={"path": str(other)}).json()
    process_source_sync(client, project["id"], second["id"], _OneCarDetector(), target_fps=10.0)
    before = len(client.get(f"/projects/{project['id']}/frames", params={"source_id": second["id"]}).json())

    client.post(f"/projects/{project['id']}/frames/sweep", params={"source_id": frames[0]["source_id"]})

    untouched = client.get(f"/projects/{project['id']}/frames", params={"source_id": second["id"]}).json()
    assert len(untouched) == before


def test_an_unknown_source_is_refused_rather_than_sweeping_everything(tmp_path):
    """The most dangerous possible reading of a bad id."""
    project, _ = _video_project_with_frames(tmp_path, "Sweep Bad Source")

    refused = client.post(f"/projects/{project['id']}/frames/sweep", params={"source_id": "not-a-source"})

    assert refused.status_code == 404
    assert client.get(f"/projects/{project['id']}/frames").json() != []


def test_a_frame_in_an_exported_dataset_is_held_back(tmp_path):
    """A version is the record of what a model was trained on, and it
    has to keep describing files that exist."""
    project, frames = _video_project_with_frames(tmp_path, "Sweep Respects Exports")
    _label(frames[0])
    _label(frames[1])
    exported = client.post(f"/projects/{project['id']}/dataset-versions", json={})
    assert exported.status_code in (200, 201), exported.text
    # Set one of them aside without removing its boxes, which is the
    # only way an exported frame stops counting as labelled - and so
    # the only way the sweep would otherwise reach it.
    client.put(f"/frames/{frames[1]['id']}/status", json={"status": "skipped"})

    done = client.post(f"/projects/{project['id']}/frames/sweep").json()

    assert done["held"] >= 1
    # Asked for by status: a set-aside frame is deliberately kept out
    # of the working queue, so the plain listing would not show it
    # whether it survived or not.
    set_aside = [f["id"] for f in client.get(
        f"/projects/{project['id']}/frames", params={"status": "skipped"}
    ).json()]
    assert frames[1]["id"] in set_aside
