"""The full frame behind a live detection.

A live stream cannot be decoded twice. Detection wrote the crop and
left the frame row with no image, so the pixels the review and the
labelling queue need were simply gone: 333 of 378 frames in a real
workspace had no image, every accepted detection was invisible in the
Label tab, and review could only ever show a tight plate crop.

A frame the model found something on is the frame the whole session
is for. It gets written.
"""

from pathlib import Path

import numpy as np
from fastapi.testclient import TestClient

from app.db.models.frame import Frame
from app.db.models.processing_run import ProcessingRun
from app.db.models.source import Source
from app.db.session import SessionLocal
from app.main import app
from app.ml.bytetrack_tracker import ByteTrackTracker
from app.ml.types import Detection
from app.services.rolling_buffer import BufferedFrame
from app.services.rtsp_session import DETECTED_FRAME_LIMIT, KeepFrames, RtspCaptureSession

client = TestClient(app)

WIDTH, HEIGHT = 128, 96


class _FindsNothing:
    model_version = "finds-nothing-v1"
    class_names = {0: "plate"}

    def detect(self, frame):
        return []


class _FindsAPlate:
    model_version = "one-plate-stub-v1"
    class_names = {0: "plate"}

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
        run = ProcessingRun(source_id=source.id, sampling_config={"protocol": "rtsp"}, status="running")
        db.add(run)
        db.commit()
        return source.id, run.id


def _session(project: dict, source_id: str, run_id: str, detector, keep: KeepFrames | None = None):
    return RtspCaptureSession(
        run_id=run_id,
        source_id=source_id,
        # Every detected frame, unless a test says otherwise: these
        # tests are about writing the frame at all. How many of one
        # vehicle are kept is tested in test_live_frames_per_vehicle.py.
        keep_frames=keep or KeepFrames(per_vehicle=0),
        adapter=None,  # nothing is started; frames are fed in by hand
        detector=detector,
        tracker=ByteTrackTracker(frame_rate=10.0),
        workspace_path=Path(project["workspace_path"]),
    )


def _feed(session: RtspCaptureSession, count: int) -> None:
    """Push frames through the processing step, as the loop does."""
    for index in range(count):
        image = np.full((HEIGHT, WIDTH, 3), 90, dtype=np.uint8)
        item = BufferedFrame(frame_index=index, timestamp_ms=index * 100, payload=image)
        session._process_one(item)


def _frames_of(source_id: str) -> list[Frame]:
    with SessionLocal() as db:
        return list(db.query(Frame).filter(Frame.source_id == source_id).all())


# --- writing the frame --------------------------------------------------------


def test_the_frame_a_detection_landed_on_is_written(tmp_path):
    project = _project("Detected Frame Kept")
    source_id, run_id = _live_source(project)
    session = _session(project, source_id, run_id, _FindsAPlate())

    _feed(session, 5)

    assert session.status().frames_saved == 5


def test_a_frame_with_no_detection_is_not_written(tmp_path):
    """Keeping every frame of a camera is what the sampling setting is
    for, and it is off by default."""
    project = _project("Empty Frame Not Kept")
    source_id, run_id = _live_source(project)
    session = _session(project, source_id, run_id, _FindsNothing())

    _feed(session, 5)

    assert session.status().frames_saved == 0


def test_a_frame_is_written_once_however_many_detections_it_carries(tmp_path):
    project = _project("Written Once")
    source_id, run_id = _live_source(project)
    session = _session(
        project, source_id, run_id, _FindsAPlate(), KeepFrames(enabled=True, every=1)
    )

    _feed(session, 4)

    assert session.status().frames_saved == 4


def test_it_stops_at_the_ceiling(tmp_path):
    """A disk with single-digit gigabytes free is the constraint the
    whole app is built around."""
    project = _project("Detected Ceiling")
    source_id, run_id = _live_source(project)
    session = _session(project, source_id, run_id, _FindsAPlate())
    session._detected_saved = DETECTED_FRAME_LIMIT

    _feed(session, 3)

    assert session.status().frames_saved == 0


# --- the row that makes it reachable ------------------------------------------


def test_the_frame_row_carries_the_image(tmp_path):
    """Without this the Label queue will not offer it: a frame of a
    live source with no image is a queue entry that errors on click."""
    project = _project("Row Has Image")
    source_id, run_id = _live_source(project)
    session = _session(project, source_id, run_id, _FindsAPlate())
    _feed(session, 3)

    session._persist(status="completed", finalize=True)

    frames = _frames_of(source_id)
    assert frames
    assert all(f.image_path and Path(f.image_path).is_file() for f in frames)


def test_the_detection_and_the_frame_agree_on_which_frame_it_is(tmp_path):
    """One row per captured frame, not one from the saver and another
    from the detector."""
    project = _project("One Row Per Frame")
    source_id, run_id = _live_source(project)
    session = _session(project, source_id, run_id, _FindsAPlate())
    _feed(session, 3)

    session._persist(status="completed", finalize=True)

    indexes = sorted(f.frame_index for f in _frames_of(source_id))
    assert indexes == sorted(set(indexes))


def test_the_kept_frame_reaches_the_labelling_queue(tmp_path):
    """The whole point: accept a detection and find it in Label."""
    project = _project("Reaches The Queue")
    source_id, run_id = _live_source(project)
    session = _session(project, source_id, run_id, _FindsAPlate())
    _feed(session, 3)
    session._persist(status="completed", finalize=True)

    queue = client.get(f"/projects/{project['id']}/frames").json()

    assert len(queue) == 3
