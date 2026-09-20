from app.db.models.ocr_candidate import OcrCandidate
from app.db.models.processing_run import ProcessingRun
from app.db.models.project import Project
from app.db.models.source import Source
from app.db.models.track import Track
from app.db.session import SessionLocal
from app.ml.bytetrack_tracker import ByteTrackTracker
from app.ml.types import PlateOcrCandidate
from app.services.ocr_processor import run_ocr_for_track
from app.services.track_processor import process_source
from app.services.workspace import create_project_workspace
from tests.stub_detector import StubDetector
from tests.stub_ocr_engine import StubOcrEngine
from tests.video_factory import create_synthetic_video


def _make_track_with_ocr_candidates(tmp_path, frame_count=60):
    """A real track produced by the full Phase 2/3 pipeline, so it has
    genuine `ocr_candidate`-flagged frame_candidates to run OCR on."""
    video_path = create_synthetic_video(tmp_path / "clip.mp4", frame_count=frame_count, fps=10.0)

    db = SessionLocal()
    project = Project(name="OCR Processor Test", workspace_path="")
    db.add(project)
    db.flush()
    workspace_path = create_project_workspace(tmp_path / "workspace_root", project.id, project.name)
    project.workspace_path = str(workspace_path)

    source = Source(
        project_id=project.id,
        type="video",
        path_or_uri=str(video_path),
        fps=10.0,
        width=64,
        height=48,
        duration_ms=frame_count * 100,
        frame_count=frame_count,
    )
    db.add(source)
    db.flush()

    run = ProcessingRun(source_id=source.id, sampling_config={"target_fps": 5.0}, status="running")
    db.add(run)
    db.flush()

    process_source(
        db=db,
        source=source,
        workspace_path=workspace_path,
        run=run,
        target_fps=5.0,
        detector=StubDetector(),
        tracker=ByteTrackTracker(frame_rate=5.0),
    )

    # The longest track: a stub detector emitting the same box every
    # frame also leaves one single-frame sighting behind, from the
    # frame before the tracker had confirmed anything.
    tracks = db.query(Track).filter(Track.run_id == run.id).all()
    track = max(tracks, key=lambda t: t.end_ts - t.start_ts)
    return db, track


def test_ocr_stops_after_first_confident_result(tmp_path):
    db, track = _make_track_with_ocr_candidates(tmp_path)
    try:
        engine = StubOcrEngine(
            responses=[
                [PlateOcrCandidate(bbox_xyxy=(0, 0, 10, 5), text="weak", confidence=0.2)],
                [PlateOcrCandidate(bbox_xyxy=(0, 0, 10, 5), text="MH12AB1234", confidence=0.95)],
                [PlateOcrCandidate(bbox_xyxy=(0, 0, 10, 5), text="should-not-be-tried", confidence=0.99)],
            ]
        )

        attempts = run_ocr_for_track(db, track, engine, confident_threshold=0.7)

        assert engine._call_count == 2  # stopped after the second (confident) frame
        assert len(attempts) == 2
        assert {a.text for a in attempts} == {"weak", "MH12AB1234"}

        selected = [a for a in attempts if a.selected]
        assert len(selected) == 1
        assert selected[0].text == "MH12AB1234"
        assert selected[0].normalized_text == "MH12AB1234"
        assert selected[0].source == "model"

        persisted = db.query(OcrCandidate).filter(OcrCandidate.track_id == track.id).all()
        assert len(persisted) == 2
    finally:
        db.close()


def test_ocr_with_no_readable_text_persists_nothing(tmp_path):
    db, track = _make_track_with_ocr_candidates(tmp_path)
    try:
        engine = StubOcrEngine(responses=[[]])
        attempts = run_ocr_for_track(db, track, engine)
        assert attempts == []
        assert db.query(OcrCandidate).filter(OcrCandidate.track_id == track.id).count() == 0
    finally:
        db.close()
