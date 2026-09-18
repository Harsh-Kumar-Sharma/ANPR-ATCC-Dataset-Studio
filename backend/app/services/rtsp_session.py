import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from app.db.models.processing_run import ProcessingRun
from app.db.session import SessionLocal
from app.ml.detector import Detector
from app.ml.tracker import Tracker
from app.services.live_preview import LivePreview, PreviewBox, PreviewFrame
from app.services.rolling_buffer import RollingFrameBuffer
from app.services.rtsp_source import RtspSourceAdapter
from app.services.track_processor import ObservationsByTrack, _Observation, observe_frame, persist_observations


@dataclass
class RtspSessionStatus:
    run_id: str
    connected: bool = False
    reconnect_attempts: int = 0
    frames_captured: int = 0
    frames_dropped: int = 0
    tracks_persisted: int = 0
    stopped: bool = False
    error: str | None = None


class RtspCaptureSession:
    """One live RTSP capture-and-process session, run on background
    threads (docs/02_IMPLEMENTATION_PLAN.md Phase 9). A local, single-
    user desktop app doesn't need a real distributed task queue for
    this - a couple of daemon threads with a stop flag is
    proportionate, consistent with this project's local-first design.

    Two threads by design, not one: a capture thread that owns the
    adapter and only ever reads frames as fast as the stream provides
    them (handling reconnects), and a processing thread that drains
    the bounded buffer and runs detect -> track -> persist. Splitting
    them is what makes the buffer meaningful - if capture and
    processing shared one loop, the buffer could never hold more than
    one frame at a time, and "bounded rolling buffer" would be a
    guarantee about nothing.
    """

    def __init__(
        self,
        run_id: str,
        adapter: RtspSourceAdapter,
        detector: Detector,
        tracker: Tracker,
        workspace_path: Path,
        buffer_maxlen: int = 300,
        persist_interval_seconds: float = 10.0,
        poll_interval_seconds: float = 0.05,
    ) -> None:
        self._run_id = run_id
        self._adapter = adapter
        self._detector = detector
        self._tracker = tracker
        self._tracks_root = workspace_path / "derived" / "tracks"
        self._buffer: RollingFrameBuffer = RollingFrameBuffer(maxlen=buffer_maxlen)
        self._persist_interval_seconds = persist_interval_seconds
        self._poll_interval_seconds = poll_interval_seconds

        self._observations_by_track: ObservationsByTrack = {}
        self._preview = LivePreview()
        self._lock = threading.Lock()
        self._status = RtspSessionStatus(run_id=run_id)
        self._stop_event = threading.Event()
        self._capture_finished = threading.Event()
        self._capture_thread: threading.Thread | None = None
        self._processing_thread: threading.Thread | None = None

    def start(self) -> None:
        self._capture_thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._processing_thread = threading.Thread(target=self._processing_loop, daemon=True)
        self._capture_thread.start()
        self._processing_thread.start()

    def stop(self) -> None:
        self._stop_event.set()

    def join(self, timeout: float | None = None) -> None:
        if self._capture_thread is not None:
            self._capture_thread.join(timeout)
        if self._processing_thread is not None:
            self._processing_thread.join(timeout)

    def status(self) -> RtspSessionStatus:
        with self._lock:
            return replace(self._status)

    def preview(self) -> PreviewFrame | None:
        """The latest processed frame with its tracked boxes drawn on, or
        None before the first frame has been processed."""
        return self._preview.latest()

    def _on_reconnect_attempt(self, attempt: int) -> None:
        with self._lock:
            self._status.reconnect_attempts = attempt

    def _capture_loop(self) -> None:
        frame_index = 0
        start_time = time.monotonic()
        try:
            if not self._adapter.connect() and not self._adapter.reconnect(
                should_stop=self._stop_event.is_set, on_attempt=self._on_reconnect_attempt
            ):
                if not self._stop_event.is_set():
                    with self._lock:
                        self._status.error = "could not establish initial connection"
                return

            with self._lock:
                self._status.connected = True

            while not self._stop_event.is_set():
                frame = self._adapter.read_frame()
                if frame is None:
                    if self._stop_event.is_set():
                        break
                    if not self._adapter.reconnect(
                        should_stop=self._stop_event.is_set, on_attempt=self._on_reconnect_attempt
                    ):
                        with self._lock:
                            if not self._stop_event.is_set():
                                self._status.error = "reconnect attempts exhausted"
                            self._status.connected = False
                        break
                    with self._lock:
                        self._status.connected = True
                        self._status.reconnect_attempts = 0
                    continue

                timestamp_ms = int((time.monotonic() - start_time) * 1000)
                with self._lock:
                    self._buffer.append(frame_index, timestamp_ms, frame)
                    self._status.frames_captured += 1
                    self._status.frames_dropped = self._buffer.dropped_count
                    self._status.reconnect_attempts = self._adapter.reconnect_attempts
                frame_index += 1
        finally:
            self._adapter.close()
            with self._lock:
                self._status.connected = False
            self._capture_finished.set()

    def _processing_loop(self) -> None:
        last_persist = time.monotonic()
        while True:
            with self._lock:
                drained = self._buffer.drain()

            for item in drained:
                tracked = observe_frame(
                    self._observations_by_track, item.payload, item.frame_index, item.timestamp_ms, self._detector, self._tracker
                )
                # Published per processed frame, not once per drained batch.
                # When detection is slower than capture the buffer backs up and
                # a single batch can span hundreds of frames, so a batch-level
                # publish froze the preview for the whole batch (measured on a
                # real 1080p clip: one new preview frame in 8 seconds while 854
                # frames arrived). LivePreview throttles, so this does not
                # JPEG-encode every frame.
                self._publish_preview(item.payload, tracked)

            capture_done = self._capture_finished.is_set()
            if capture_done and not drained:
                break

            if time.monotonic() - last_persist >= self._persist_interval_seconds:
                self._persist(status="running")
                last_persist = time.monotonic()

            if not drained:
                time.sleep(self._poll_interval_seconds)

        self._persist(status="failed" if self._status_snapshot().error else "completed", finalize=True)

    def _publish_preview(self, frame: np.ndarray, tracked: list[tuple[int, _Observation]]) -> None:
        class_names = self._detector.class_names
        boxes = [
            PreviewBox(
                bbox_xyxy=observation.bbox,
                label=f"#{track_id} {class_names.get(observation.class_id, observation.class_id)} "
                f"{observation.confidence:.0%}",
                track_id=track_id,
            )
            for track_id, observation in tracked
        ]
        self._preview.update(frame, boxes)

    def _status_snapshot(self) -> RtspSessionStatus:
        with self._lock:
            return replace(self._status)

    def _persist(self, status: str, finalize: bool = False) -> None:
        db = SessionLocal()
        try:
            run = db.get(ProcessingRun, self._run_id)
            if run is None:
                return
            tracks = persist_observations(db, run, self._observations_by_track, self._tracks_root, self._detector.class_names)
            self._observations_by_track.clear()
            with self._lock:
                self._status.tracks_persisted += len(tracks)
                if finalize:
                    run.status = status
                    run.error_message = self._status.error
                    run.sampled_frame_count = self._status.frames_captured
                    run.completed_at = datetime.now(timezone.utc)
                    self._status.stopped = True
            db.commit()
        finally:
            db.close()
