import time

import numpy as np

#: Real frame arrival is inherently rate-limited by network/decode; an
#: unthrottled fake would produce thousands of reads within a test's
#: sleep window, many sharing the same millisecond timestamp (which
#: the tracker's duplicate-timestamp guard skips). This keeps the fake
#: realistic without slowing tests meaningfully.
_FAKE_FRAME_INTERVAL_SECONDS = 0.005

from app.db.models.frame_candidate import FrameCandidate
from app.db.models.processing_run import ProcessingRun
from app.db.models.project import Project
from app.db.models.source import Source
from app.db.models.track import Track
from app.db.session import SessionLocal
from app.ml.bytetrack_tracker import ByteTrackTracker
from app.ml.types import Detection
from app.services.rolling_buffer import RollingFrameBuffer
from app.services.rtsp_session import RtspCaptureSession
from app.services.rtsp_source import RtspSourceAdapter
from app.services.workspace import create_project_workspace


class _StaticBoxDetector:
    """Unlike tests/stub_detector.py's StubDetector, this never drifts
    the bbox - needed here because a live session can run for an
    unbounded number of frames within the test's sleep window, and a
    drifting box would eventually leave the frame."""

    model_version = "static-box-stub-v1"
    class_names = {0: "car"}

    def detect(self, frame: np.ndarray) -> list[Detection]:
        return [Detection(bbox_xyxy=(10, 10, 60, 40), class_id=0, confidence=0.9)]


class InfiniteFrameConnection:
    """A fake RTSP connection that never runs out of frames, until
    told to fail. Models a real, indefinitely-running camera stream -
    a live session only ever stops via explicit stop() or an
    unrecoverable disconnect, never by "running out" of input."""

    def __init__(self, fail_after: int | None = None) -> None:
        self._reads = 0
        self._fail_after = fail_after
        self.opens = 0

    def open(self) -> bool:
        self.opens += 1
        return True

    def read(self) -> np.ndarray | None:
        time.sleep(_FAKE_FRAME_INTERVAL_SECONDS)
        if self._fail_after is not None and self._reads >= self._fail_after:
            return None
        self._reads += 1
        return np.full((48, 64, 3), fill_value=128, dtype=np.uint8)

    def release(self) -> None:
        pass


def _setup(tmp_path, name: str):
    db = SessionLocal()
    project = Project(name=name, workspace_path="")
    db.add(project)
    db.flush()
    workspace_path = create_project_workspace(tmp_path / "workspace_root", project.id, project.name)
    project.workspace_path = str(workspace_path)

    source = Source(
        project_id=project.id,
        type="rtsp",
        path_or_uri="rtsp://fake/stream",
        fps=10.0,
        width=0,
        height=0,
        duration_ms=0,
        frame_count=0,
    )
    db.add(source)
    db.flush()

    run = ProcessingRun(source_id=source.id, sampling_config={"protocol": "rtsp"}, status="running")
    db.add(run)
    db.commit()

    return db, project, source, run, workspace_path


def test_session_captures_and_persists_tracks_until_stopped(tmp_path):
    db, project, source, run, workspace_path = _setup(tmp_path, "RTSP Session Capture Test")
    try:
        adapter = RtspSourceAdapter(connection_factory=lambda: InfiniteFrameConnection())
        session = RtspCaptureSession(
            run_id=run.id,
            adapter=adapter,
            detector=_StaticBoxDetector(),
            tracker=ByteTrackTracker(frame_rate=10.0),
            workspace_path=workspace_path,
            buffer_maxlen=50,
            persist_interval_seconds=1000,  # avoid an interim persist racing the final one in this short test
        )
        session.start()
        time.sleep(0.5)
        session.stop()
        session.join(timeout=10)

        status = session.status()
        assert status.stopped is True
        assert status.connected is False
        assert status.frames_captured > 0
        assert status.tracks_persisted >= 1
        assert status.error is None

        db.refresh(run)
        assert run.status == "completed"
        assert run.sampled_frame_count == status.frames_captured

        tracks = db.query(Track).filter(Track.run_id == run.id).all()
        assert len(tracks) >= 1
        frames = db.query(FrameCandidate).filter(FrameCandidate.track_id == tracks[0].id).all()
        assert len(frames) >= 1
    finally:
        db.close()


def test_session_reconnects_after_a_mid_stream_drop(tmp_path):
    db, project, source, run, workspace_path = _setup(tmp_path, "RTSP Session Reconnect Test")
    try:
        connections: list[InfiniteFrameConnection] = []

        def factory():
            # First connection drops after 5 frames; every connection after
            # that (i.e. the reconnect) stays up for the rest of the test.
            conn = InfiniteFrameConnection(fail_after=5 if not connections else None)
            connections.append(conn)
            return conn

        from app.services.rtsp_source import RtspAdapterConfig

        adapter = RtspSourceAdapter(
            connection_factory=factory,
            config=RtspAdapterConfig(initial_backoff_seconds=0.05),
        )
        session = RtspCaptureSession(
            run_id=run.id,
            adapter=adapter,
            detector=_StaticBoxDetector(),
            tracker=ByteTrackTracker(frame_rate=10.0),
            workspace_path=workspace_path,
            buffer_maxlen=50,
            persist_interval_seconds=1000,
        )
        session.start()
        time.sleep(1.0)  # long enough to hit the drop, reconnect quickly, and capture more afterward
        session.stop()
        session.join(timeout=10)

        status = session.status()
        assert status.stopped is True
        assert status.error is None  # recovered, not a fatal failure
        assert len(connections) >= 2  # the original connection plus at least one reconnect
        assert status.frames_captured > 5  # captured frames both before and after the drop
    finally:
        db.close()


def test_session_reports_error_when_it_cannot_connect_at_all(tmp_path):
    db, project, source, run, workspace_path = _setup(tmp_path, "RTSP Session Connect Failure Test")
    try:
        class NeverOpens:
            def open(self) -> bool:
                return False

            def read(self):
                return None

            def release(self) -> None:
                pass

        from app.services.rtsp_source import RtspAdapterConfig

        adapter = RtspSourceAdapter(
            connection_factory=lambda: NeverOpens(),
            config=RtspAdapterConfig(max_reconnect_attempts=1, initial_backoff_seconds=0.01),
            sleep=lambda _s: None,
        )
        session = RtspCaptureSession(
            run_id=run.id,
            adapter=adapter,
            detector=_StaticBoxDetector(),
            tracker=ByteTrackTracker(frame_rate=10.0),
            workspace_path=workspace_path,
        )
        session.start()
        session.join(timeout=10)

        status = session.status()
        assert status.stopped is True
        assert status.error is not None
        assert status.frames_captured == 0

        db.refresh(run)
        assert run.status == "failed"
        assert run.error_message == status.error
    finally:
        db.close()


def test_bounded_buffer_never_grows_past_its_configured_maxlen():
    # Not session-specific, but documents the guarantee the session
    # relies on: no matter how much faster capture is than draining,
    # the buffer itself can never exceed maxlen.
    buf: RollingFrameBuffer = RollingFrameBuffer(maxlen=5)
    for i in range(1000):
        buf.append(i, i, object())
        assert len(buf) <= 5
    assert buf.dropped_count == 1000 - 5
