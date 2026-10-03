"""Finding the vehicles the plate model misses.

A plate model cannot say what it did not see, so the misses were
invisible: nobody could tell which cars went by without a plate box.
Alongside it, a small vehicle model now looks; a vehicle with no plate
found on it keeps its frame, marked as a possible miss.
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
from app.services.rtsp_session import POSSIBLE_MISS, KeepFrames, RtspCaptureSession

client = TestClient(app)

WIDTH, HEIGHT = 640, 480
CAR = (200.0, 150.0, 440.0, 400.0)  # well inside the frame, ~20% of it


class _Plates:
    model_version = "plates-v1"
    class_names = {0: "plate"}

    def __init__(self, boxes):
        self.boxes = boxes

    def detect(self, frame):
        return [Detection(bbox_xyxy=b, class_id=0, confidence=0.9) for b in self.boxes]


class _Vehicles:
    model_version = "vehicles-v1"
    class_names = {2: "car"}

    def __init__(self, boxes):
        self.boxes = boxes
        self.calls = 0

    def detect(self, frame):
        self.calls += 1
        return [Detection(bbox_xyxy=b, class_id=2, confidence=0.8) for b in self.boxes]


def _session(plates, vehicles, keep: KeepFrames | None = None) -> tuple[RtspCaptureSession, str]:
    project = client.post("/projects", json={"name": "Misses"}).json()
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
    session = RtspCaptureSession(
        run_id=run_id,
        source_id=source_id,
        keep_frames=keep or KeepFrames(),
        adapter=None,
        detector=plates,
        tracker=ByteTrackTracker(frame_rate=10.0),
        workspace_path=Path(project["workspace_path"]),
        vehicle_detector=vehicles,
    )
    return session, source_id


def _feed(session: RtspCaptureSession, count: int, step_ms: int = 100) -> None:
    for index in range(count):
        image = np.full((HEIGHT, WIDTH, 3), 90, dtype=np.uint8)
        session._process_one(BufferedFrame(frame_index=index, timestamp_ms=index * step_ms, payload=image))


def test_a_vehicle_with_no_plate_found_keeps_its_frame():
    session, _ = _session(_Plates([]), _Vehicles([CAR]))
    _feed(session, 1)
    assert session.status().possible_misses_saved == 1


def test_a_vehicle_whose_plate_was_found_is_not_a_miss():
    plate_on_car = (300.0, 340.0, 340.0, 360.0)
    session, _ = _session(_Plates([plate_on_car]), _Vehicles([CAR]))
    _feed(session, 20)
    assert session.status().possible_misses_saved == 0


def test_a_plate_elsewhere_does_not_cover_this_vehicle():
    plate_off_car = (500.0, 20.0, 540.0, 40.0)
    session, _ = _session(_Plates([plate_off_car]), _Vehicles([CAR]))
    _feed(session, 1)
    assert session.status().possible_misses_saved == 1


def test_a_vehicle_cut_off_by_the_edge_is_not_a_miss():
    entering = (0.0, 150.0, 200.0, 400.0)
    session, _ = _session(_Plates([]), _Vehicles([entering]))
    _feed(session, 1)
    assert session.status().possible_misses_saved == 0


def test_a_vehicle_too_far_away_is_not_a_miss():
    tiny = (300.0, 200.0, 320.0, 215.0)
    session, _ = _session(_Plates([]), _Vehicles([tiny]))
    _feed(session, 1)
    assert session.status().possible_misses_saved == 0


def test_the_same_miss_is_not_kept_every_frame():
    # Three seconds of one unplated car at 10 fps: thirty frames.
    session, _ = _session(_Plates([]), _Vehicles([CAR]))
    _feed(session, 30)
    # One every 1.5 s at most.
    assert session.status().possible_misses_saved == 2


def test_the_vehicle_model_is_not_run_on_every_frame():
    vehicles = _Vehicles([])
    session, _ = _session(_Plates([]), vehicles)
    _feed(session, 30)  # 3 s
    assert vehicles.calls <= 11  # one per 300 ms


def test_the_frame_is_marked_as_a_possible_miss():
    session, source_id = _session(_Plates([]), _Vehicles([CAR]))
    _feed(session, 1)
    session._persist(status="running")

    with SessionLocal() as db:
        frames = db.query(Frame).filter(Frame.source_id == source_id).all()
    assert [f.selection_reason for f in frames] == [POSSIBLE_MISS]


def test_it_can_be_turned_off():
    session, _ = _session(_Plates([]), _Vehicles([CAR]), KeepFrames(find_misses=False))
    _feed(session, 10)
    assert session.status().possible_misses_saved == 0


def test_a_failing_vehicle_model_does_not_stop_the_capture():
    class _Broken:
        model_version = "broken"
        class_names = {}

        def detect(self, frame):
            raise RuntimeError("CUDA out of memory")

    session, _ = _session(_Plates([]), _Broken())
    _feed(session, 5)
    assert session.status().possible_misses_saved == 0


def test_no_vehicle_model_is_loaded_unless_the_session_model_finds_plates():
    from app.api.rtsp import _vehicle_detector_for

    loaded = []

    def provider(model_id):
        loaded.append(model_id)
        return _Vehicles([])

    assert _vehicle_detector_for(_Plates([]), True, provider) is not None
    assert _vehicle_detector_for(_Vehicles([]), True, provider) is None
    assert _vehicle_detector_for(_Plates([]), False, provider) is None
    assert loaded == ["yolo26n"]
