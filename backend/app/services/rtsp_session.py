import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np

from app.db.models.processing_run import ProcessingRun
from app.db.session import SessionLocal
from app.ml.detector import Detector
from app.ml.tracker import Tracker
from app.services.frame_materializer import frames_root, get_or_create_frame
from app.services.live_preview import LivePreview, PreviewBox, PreviewFrame
from app.services.rolling_buffer import RollingFrameBuffer
from app.services.rtsp_source import RtspSourceAdapter
from app.services.track_processor import ObservationsByTrack, _Observation, observe_frame, persist_observations

logger = logging.getLogger(__name__)

#: How many frames a session will write because a detection landed on
#: them, however long it runs.
#:
#: Unbounded is not an option on the disk this app is built for, but
#: the ceiling is high: these are the frames the session exists to
#: produce, and a session that hits it has already given a person
#: more labelling than a day's work.
DETECTED_FRAME_LIMIT = 5000

#: Why a frame kept for this reason is in the labelling queue.
POSSIBLE_MISS = "possible_miss"


def _unplated_vehicle(
    vehicle: tuple[float, float, float, float],
    plates: list[tuple[float, float, float, float]],
    width: int,
    height: int,
    min_area_ratio: float,
) -> bool:
    """A vehicle near enough to label, fully in frame, with no plate on it.

    A vehicle cut off by the frame edge is still coming in or already
    leaving, and its plate may simply be out of the picture - counting
    those would fill the queue with frames that are not misses at all.
    """
    x1, y1, x2, y2 = vehicle
    if (x2 - x1) * (y2 - y1) < min_area_ratio * width * height:
        return False
    margin_x, margin_y = width * 0.01, height * 0.01
    if x1 <= margin_x or y1 <= margin_y or x2 >= width - margin_x or y2 >= height - margin_y:
        return False
    for px1, py1, px2, py2 in plates:
        cx, cy = (px1 + px2) / 2, (py1 + py2) / 2
        if x1 <= cx <= x2 and y1 <= cy <= y2:
            return False
    return True


@dataclass
class KeepFrames:
    """Whether to keep the frames themselves, and how many.

    A live session that detects nothing leaves nothing to label -
    2,453 frames captured and not one of them reviewable. Keeping the
    frames is what makes a session useful when the model is wrong, or
    when there is no model worth trusting yet.

    Bounded on purpose. Every frame of a long session at 1080p is
    gigabytes, and this runs on a disk with single-digit gigabytes
    free, so it keeps one in ``every`` and stops at ``max_frames``.
    """

    enabled: bool = False
    #: Keep one frame in this many. Consecutive frames of a camera
    #: mostly show the same thing, so this costs little and saves a
    #: lot.
    every: int = 10
    #: A ceiling the session cannot talk its way past, however long
    #: it runs.
    max_frames: int = 2000
    #: How many frames to keep of any one vehicle the model tracks. 0
    #: keeps every frame a detection lands on, which is what it did
    #: before: a vehicle in view for two seconds came back as forty
    #: near-identical frames to label.
    per_vehicle: int = 3
    #: Frames of one vehicle closer together than this are the same
    #: picture twice.
    per_vehicle_gap_ms: int = 500
    #: How much a vehicle's box must have grown or shrunk since its last
    #: kept frame before another is worth keeping - far, middle, near.
    per_vehicle_scale_step: float = 1.5
    #: Look for vehicles the plate model found no plate on, and keep
    #: those frames - the ones worth labelling most. Needs a vehicle
    #: detector handed to the session; without one this does nothing.
    find_misses: bool = True
    #: How often to run the vehicle detector. It is a second model on
    #: the same GPU, so not on every frame.
    miss_check_every_ms: int = 300
    #: One possible-miss frame at most this often, so a vehicle the
    #: model never sees is kept once or twice, not for every check.
    miss_gap_ms: int = 1500
    miss_max_frames: int = 1000
    #: A vehicle smaller than this share of the frame is too far away
    #: for its plate to be labellable, so it is not counted as missed.
    miss_min_area_ratio: float = 0.01


@dataclass
class _SavedFrame:
    """A frame written to disk, waiting for its database row."""

    frame_index: int
    timestamp_ms: int
    width: int
    height: int
    path: Path
    #: Why it was kept, for the labelling queue. None for the usual reasons.
    reason: str | None = None


@dataclass
class RtspSessionStatus:
    run_id: str
    connected: bool = False
    reconnect_attempts: int = 0
    frames_captured: int = 0
    frames_dropped: int = 0
    tracks_persisted: int = 0
    #: How many captured frames were kept for labelling.
    frames_saved: int = 0
    #: Frames with a detection that were not kept, because that vehicle
    #: already had enough frames.
    frames_skipped_repeat: int = 0
    #: Frames kept because a vehicle was seen and no plate on it was.
    possible_misses_saved: int = 0
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
        source_id: str | None = None,
        keep_frames: KeepFrames | None = None,
        vehicle_detector: Detector | None = None,
    ) -> None:
        self._run_id = run_id
        self._adapter = adapter
        self._detector = detector
        self._tracker = tracker
        self._tracks_root = workspace_path / "derived" / "tracks"
        self._buffer: RollingFrameBuffer = RollingFrameBuffer(maxlen=buffer_maxlen)
        self._persist_interval_seconds = persist_interval_seconds
        self._poll_interval_seconds = poll_interval_seconds

        self._source_id = source_id
        self._keep_frames = keep_frames or KeepFrames()
        # Frames the camera's own, kept where every other full frame
        # of this source lives, so deleting the source takes them too.
        self._frames_dir = frames_root(workspace_path) / source_id if source_id else None
        self._saved: list[_SavedFrame] = []
        self._saved_indexes: set[int] = set()
        self._frames_saved = 0
        #: Counted apart from the total so that frames kept because a
        #: detection landed on them cannot eat the sampling setting's
        #: budget, or the other way round.
        self._sampled_saved = 0
        self._detected_saved = 0
        #: Per tracked vehicle: how many of its frames were kept, when
        #: the last one was, and how big its box was then.
        self._kept_by_track: dict[int, tuple[int, int, float]] = {}
        #: Finds vehicles, to notice the ones the plate model did not.
        self._vehicle_detector = vehicle_detector
        self._last_miss_check_ms: int | None = None
        self._last_miss_saved_ms: int | None = None
        self._misses_saved = 0

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
                self._process_one(item)

            capture_done = self._capture_finished.is_set()
            if capture_done and not drained:
                break

            if time.monotonic() - last_persist >= self._persist_interval_seconds:
                self._persist(status="running")
                last_persist = time.monotonic()

            if not drained:
                time.sleep(self._poll_interval_seconds)

        self._persist(status="failed" if self._status_snapshot().error else "completed", finalize=True)

    def _process_one(self, item) -> None:
        """Detect, track, and keep whatever of this frame is worth keeping."""
        tracked = observe_frame(
            self._observations_by_track,
            item.payload,
            item.frame_index,
            item.timestamp_ms,
            self._detector,
            self._tracker,
            # A live frame is gone once it has been processed:
            # there is no file to cut the crop out of later.
            keep_crop=True,
        )
        self._maybe_keep_frame(item)
        self._maybe_keep_miss(item, tracked)
        if tracked:
            if self._worth_keeping(tracked, item.timestamp_ms):
                self._keep_detected_frame(item)
            else:
                with self._lock:
                    self._status.frames_skipped_repeat += 1
        # Published per processed frame, not once per drained batch.
        # When detection is slower than capture the buffer backs up and
        # a single batch can span hundreds of frames, so a batch-level
        # publish froze the preview for the whole batch (measured on a
        # real 1080p clip: one new preview frame in 8 seconds while 854
        # frames arrived). LivePreview throttles, so this does not
        # JPEG-encode every frame.
        self._publish_preview(item.payload, tracked)

    def _worth_keeping(self, tracked: list[tuple[int, _Observation]], timestamp_ms: int) -> bool:
        """Whether this frame shows a vehicle not already kept enough.

        A vehicle is kept when it first appears, then again only once
        its box has grown or shrunk by ``per_vehicle_scale_step`` - it
        has come noticeably closer, or gone further away - and at least
        ``per_vehicle_gap_ms`` later, up to ``per_vehicle`` frames. That
        gives far, middle and near for an approaching vehicle and the
        same, reversed, for one driving away, instead of every frame
        it was in view.

        One frame can serve several vehicles; it is kept if any of them
        wants it, and counts for each of those.
        """
        keep = self._keep_frames
        if keep.per_vehicle <= 0:
            return True
        wanted: list[tuple[int, float]] = []
        for track_id, observation in tracked:
            # A track's first frame carries a provisional id that is
            # replaced once it is confirmed; counting it would keep the
            # same vehicle twice, under two names.
            if not observation.confirmed:
                continue
            x1, y1, x2, y2 = observation.bbox
            area = max(1.0, (x2 - x1) * (y2 - y1))
            seen = self._kept_by_track.get(track_id)
            if seen is None:
                wanted.append((track_id, area))
                continue
            count, last_ms, last_area = seen
            if count >= keep.per_vehicle or timestamp_ms - last_ms < keep.per_vehicle_gap_ms:
                continue
            change = max(area, last_area) / min(area, last_area)
            if change >= keep.per_vehicle_scale_step:
                wanted.append((track_id, area))
        for track_id, area in wanted:
            count = self._kept_by_track.get(track_id, (0, 0, 0.0))[0]
            self._kept_by_track[track_id] = (count + 1, timestamp_ms, area)
        return bool(wanted)

    def _maybe_keep_miss(self, item, tracked: list[tuple[int, _Observation]]) -> None:
        """Keep the frame if a vehicle is on it and the plate model
        found no plate on that vehicle.

        The plate model cannot say what it did not see. A vehicle
        detector can: a car with no plate box anywhere on it is either
        a plate the model missed, or one too dirty, dark or covered to
        read - both exactly what the next round of labelling needs.
        """
        keep = self._keep_frames
        if not keep.find_misses or self._vehicle_detector is None:
            return
        if self._misses_saved >= keep.miss_max_frames:
            return
        now = item.timestamp_ms
        if self._last_miss_check_ms is not None and now - self._last_miss_check_ms < keep.miss_check_every_ms:
            return
        if self._last_miss_saved_ms is not None and now - self._last_miss_saved_ms < keep.miss_gap_ms:
            return
        self._last_miss_check_ms = now

        try:
            vehicles = self._vehicle_detector.detect(item.payload)
        except Exception:  # noqa: BLE001 - finding misses must never stop a capture
            logger.warning("Vehicle check failed on live frame %s", item.frame_index, exc_info=True)
            return

        height, width = item.payload.shape[:2]
        plates = [observation.bbox for _, observation in tracked]
        if not any(
            _unplated_vehicle(vehicle.bbox_xyxy, plates, width, height, keep.miss_min_area_ratio) for vehicle in vehicles
        ):
            return
        if self._write_frame(item, reason=POSSIBLE_MISS):
            self._misses_saved += 1
            self._last_miss_saved_ms = now
            with self._lock:
                self._status.possible_misses_saved = self._misses_saved

    def _keep_detected_frame(self, item) -> None:
        """Write the full frame a detection landed on, asked for or not.

        The crop alone is not enough. Reviewing a plate means seeing
        the vehicle it is on, and labelling means a frame to draw on -
        neither of which a 200x40 cut-out gives you. A live stream
        cannot be decoded a second time, so if this frame is not
        written now those pixels are gone: the detection survives as a
        row pointing at a frame nobody can open, which is how a real
        workspace ended up with 333 frames without images and every
        accepted detection missing from the Label tab.
        """
        if self._detected_saved >= DETECTED_FRAME_LIMIT:
            return
        if self._write_frame(item):
            self._detected_saved += 1

    def _maybe_keep_frame(self, item) -> None:
        """Write one captured frame to disk, if sampling asked for it.

        Separate from the detected frames above: this is the setting
        that keeps frames whether or not the model found anything,
        for when the model is wrong or there is no model worth
        trusting yet.
        """
        keep = self._keep_frames
        if not keep.enabled:
            return
        if self._sampled_saved >= keep.max_frames:
            return
        if keep.every > 1 and item.frame_index % keep.every != 0:
            return
        if self._write_frame(item):
            self._sampled_saved += 1

    def _write_frame(self, item, reason: str | None = None) -> bool:
        """Write one captured frame to disk and queue its database row.

        Written here in the processing thread rather than at capture:
        the capture thread's job is to not miss frames, and a JPEG
        encode in that loop is the kind of thing that makes it miss
        frames.

        A failure to write is logged and dropped. Losing one frame is
        not worth ending a live capture over.
        """
        if self._frames_dir is None:
            return False
        # Sampling and detection both want this frame often enough
        # that writing it twice would be the normal case.
        if item.frame_index in self._saved_indexes:
            return False

        path = self._frames_dir / f"frame_{item.frame_index:06d}.jpg"
        try:
            self._frames_dir.mkdir(parents=True, exist_ok=True)
            if not cv2.imwrite(str(path), item.payload):
                raise OSError(f"cv2 could not write {path}")
        except OSError:
            logger.warning("Could not keep frame %s of the live session", item.frame_index, exc_info=True)
            return False

        height, width = item.payload.shape[:2]
        self._saved.append(
            _SavedFrame(
                frame_index=item.frame_index,
                timestamp_ms=item.timestamp_ms,
                width=width,
                height=height,
                path=path,
                reason=reason,
            )
        )
        self._saved_indexes.add(item.frame_index)
        self._frames_saved += 1
        with self._lock:
            self._status.frames_saved = self._frames_saved
        return True

    def _persist_saved_frames(self, db) -> None:
        """Give the frames written since last time their database rows.

        Rows here rather than at write time because this is the only
        place the session holds a database session, and because a
        frame on disk with no row is recoverable while a row with no
        frame is a broken image in the labelling queue.
        """
        if not self._saved:
            return
        pending, self._saved = self._saved, []
        for saved in pending:
            frame = get_or_create_frame(
                db,
                source_id=self._source_id,
                frame_index=saved.frame_index,
                timestamp_ms=saved.timestamp_ms,
                width=saved.width,
                height=saved.height,
            )
            frame.image_path = str(saved.path)
            if saved.reason and not frame.selection_reason:
                frame.selection_reason = saved.reason

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
            # Frames first: they and the detections resolve to the
            # same rows, and this way a row is never briefly a frame
            # with no image that something else could act on.
            self._persist_saved_frames(db)
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
