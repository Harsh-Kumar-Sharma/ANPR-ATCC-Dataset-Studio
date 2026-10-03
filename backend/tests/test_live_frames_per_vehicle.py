"""A vehicle in view for two seconds is not forty frames to label.

Every frame a detection landed on used to be written, so the same car
came back five, six, seven times - and a slow truck far more. Now each
tracked vehicle is kept far, middle and near: when it first appears,
then only once it has come noticeably closer (or gone further away).
"""

from pathlib import Path

import numpy as np
from fastapi.testclient import TestClient

from app.db.models.processing_run import ProcessingRun
from app.db.models.source import Source
from app.db.session import SessionLocal
from app.main import app
from app.ml.bytetrack_tracker import ByteTrackTracker
from app.ml.types import Detection
from app.services.rolling_buffer import BufferedFrame
from app.services.rtsp_session import KeepFrames, RtspCaptureSession

client = TestClient(app)

WIDTH, HEIGHT = 640, 480


class _StandingStill:
    """One plate that never moves: the same picture, frame after frame."""

    model_version = "still-v1"
    class_names = {0: "plate"}

    def detect(self, frame):
        return [Detection(bbox_xyxy=(300.0, 200.0, 340.0, 220.0), class_id=0, confidence=0.9)]


class _Approaching:
    """One plate that grows steadily, as a vehicle driving at the camera."""

    model_version = "approach-v1"
    class_names = {0: "plate"}

    def __init__(self):
        self.calls = 0

    def detect(self, frame):
        grow = 1.0 + self.calls * 0.08
        self.calls += 1
        half_w, half_h = 20.0 * grow, 10.0 * grow
        return [Detection(bbox_xyxy=(320 - half_w, 240 - half_h, 320 + half_w, 240 + half_h), class_id=0, confidence=0.9)]


def _session(detector, keep: KeepFrames) -> RtspCaptureSession:
    project = client.post("/projects", json={"name": "Per Vehicle"}).json()
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
        source_id, run_id = source.id, run.id
    return RtspCaptureSession(
        run_id=run_id,
        source_id=source_id,
        keep_frames=keep,
        adapter=None,
        detector=detector,
        tracker=ByteTrackTracker(frame_rate=10.0),
        workspace_path=Path(project["workspace_path"]),
    )


def _feed(session: RtspCaptureSession, count: int, step_ms: int = 100) -> None:
    for index in range(count):
        image = np.full((HEIGHT, WIDTH, 3), 90, dtype=np.uint8)
        session._process_one(BufferedFrame(frame_index=index, timestamp_ms=index * step_ms, payload=image))


def test_a_vehicle_that_does_not_change_is_kept_once():
    session = _session(_StandingStill(), KeepFrames(per_vehicle=3))
    _feed(session, 40)

    status = session.status()
    assert status.frames_saved == 1
    assert status.frames_skipped_repeat == 39


def test_an_approaching_vehicle_is_kept_far_middle_and_near():
    session = _session(_Approaching(), KeepFrames(per_vehicle=3))
    _feed(session, 40)

    assert session.status().frames_saved == 3


def test_the_limit_is_the_setting():
    session = _session(_Approaching(), KeepFrames(per_vehicle=2))
    _feed(session, 40)

    assert session.status().frames_saved == 2


def test_frames_too_close_in_time_are_not_both_kept():
    # The box grows fast, but every frame is 50 ms apart: within the gap.
    session = _session(_Approaching(), KeepFrames(per_vehicle=3, per_vehicle_gap_ms=10_000))
    _feed(session, 40, step_ms=50)

    assert session.status().frames_saved == 1


def test_zero_keeps_every_frame_as_before():
    session = _session(_StandingStill(), KeepFrames(per_vehicle=0))
    _feed(session, 10)

    assert session.status().frames_saved == 10


def test_the_start_request_carries_the_setting():
    from app.schemas.rtsp import RtspStartRequest

    assert RtspStartRequest(rtsp_url="rtsp://x").frames_per_vehicle == 3
    assert RtspStartRequest(rtsp_url="rtsp://x", frames_per_vehicle=0).frames_per_vehicle == 0
