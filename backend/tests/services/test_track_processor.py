from pathlib import Path

import pytest

from app.db.models.frame_candidate import FrameCandidate
from app.db.models.processing_run import ProcessingRun
from app.db.models.project import Project
from app.db.models.source import Source
from app.db.models.track import Track
from app.db.session import SessionLocal
from app.ml.bytetrack_tracker import ByteTrackTracker
from app.services.track_processor import process_source
from app.services.workspace import create_project_workspace
from tests.stub_detector import StubDetector
from tests.video_factory import create_synthetic_video


def test_process_source_persists_track_with_frame_candidates(tmp_path):
    video_path = create_synthetic_video(tmp_path / "clip.mp4", frame_count=30, fps=10.0)

    with SessionLocal() as db:
        project = Project(name="Track Processor Test", workspace_path="")
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
            duration_ms=3000,
            frame_count=30,
        )
        db.add(source)
        db.flush()

        run = ProcessingRun(source_id=source.id, sampling_config={"target_fps": 5.0}, status="running")
        db.add(run)
        db.flush()

        result_run = process_source(
            db=db,
            source=source,
            workspace_path=workspace_path,
            run=run,
            target_fps=5.0,
            detector=StubDetector(),
            tracker=ByteTrackTracker(frame_rate=5.0),
        )

        assert result_run.status == "completed"
        assert result_run.sampled_frame_count == 15  # 30 frames @10fps sampled at 5fps

        tracks = db.query(Track).filter(Track.run_id == run.id).all()
        assert len(tracks) == 1
        track = tracks[0]
        assert track.start_ts < track.end_ts
        assert track.review_status == "unreviewed"
        assert track.bucket in {"BEST_DETECTION", "HARD", "FAILED"}

        frames = (
            db.query(FrameCandidate)
            .filter(FrameCandidate.track_id == track.id)
            .order_by(FrameCandidate.frame_index)
            .all()
        )
        # ByteTrack needs 2 consecutive frames to activate a track, so
        # the very first observation never gets a track id.
        assert len(frames) == 14
        assert [f.frame_index for f in frames] == sorted(f.frame_index for f in frames)

        first = frames[0]
        assert first.detector_class == "car"
        # Round-trips through a numpy float32 array inside the tracker.
        assert first.detector_confidence == pytest.approx(0.9, abs=1e-6)
        assert first.bbox_json[2] > first.bbox_json[0]
        assert first.bbox_json[3] > first.bbox_json[1]
        assert Path(first.image_path).is_file()
        assert str(track.id) in first.image_path

        # Phase 3 quality signals are populated for every frame.
        for frame in frames:
            assert frame.blur_score is not None and 0.0 < frame.blur_score <= 1.0
            assert frame.sharpness_score is not None and frame.sharpness_score >= 0.0
            assert frame.area_ratio is not None and 0.0 < frame.area_ratio <= 1.0
            assert frame.flags_json is not None
            assert set(frame.flags_json.keys()) == {"truncated", "quality_score", "roles"}

        # Exactly one frame is the BEST_DETECTION representative; a
        # bounded shortlist is flagged as OCR candidates.
        best_detection_frames = [f for f in frames if "best_detection" in f.flags_json["roles"]]
        ocr_candidate_frames = [f for f in frames if "ocr_candidate" in f.flags_json["roles"]]
        assert len(best_detection_frames) == 1
        assert 1 <= len(ocr_candidate_frames) <= 3


def test_process_source_with_no_detections_produces_no_tracks(tmp_path):
    video_path = create_synthetic_video(tmp_path / "clip.mp4", frame_count=10, fps=10.0)

    with SessionLocal() as db:
        project = Project(name="No Detections Test", workspace_path="")
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
            duration_ms=1000,
            frame_count=10,
        )
        db.add(source)
        db.flush()

        run = ProcessingRun(source_id=source.id, sampling_config={"target_fps": 5.0}, status="running")
        db.add(run)
        db.flush()

        result_run = process_source(
            db=db,
            source=source,
            workspace_path=workspace_path,
            run=run,
            target_fps=5.0,
            detector=StubDetector(box_per_frame={}),  # never detects anything
            tracker=ByteTrackTracker(frame_rate=5.0),
        )

        assert result_run.status == "completed"
        assert db.query(Track).filter(Track.run_id == run.id).count() == 0
