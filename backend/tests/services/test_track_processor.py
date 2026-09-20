from pathlib import Path

import pytest

from app.db.models.frame_candidate import FrameCandidate
from app.db.models.processing_run import ProcessingRun
from app.db.models.project import Project
from app.db.models.source import Source
from app.db.models.track import Track
from app.db.session import SessionLocal
from app.ml.bytetrack_tracker import ByteTrackTracker
from app.services.track_processor import _Observation, drop_sightings_absorbed_by_tracks, process_source
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

        # One, still: this vehicle is followed, so the unconfirmed
        # first frame is absorbed into its track rather than left
        # standing as a one-frame ghost in front of it.
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
        # No crop file: an offline run leaves the pixels in the video
        # and cuts them out again when something asks to look. At two
        # vehicles a frame over a long clip, writing them all is
        # gigabytes before anything has been reviewed.
        assert first.image_path is None

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


# --- detections the tracker cannot follow -------------------------------------


def _sighting(frame_index: int, x: float, confirmed: bool = False) -> _Observation:
    return _Observation(
        frame_index=frame_index,
        timestamp_ms=frame_index * 140,
        bbox=(x, 500.0, x + 90.0, 530.0),
        class_id=0,
        confidence=0.55,
        confirmed=confirmed,
        crop=None,
        sharpness_score=100.0,
        blur_score=0.5,
        area_ratio=0.01,
        truncated=False,
        frame_width=1920,
        frame_height=1080,
    )


def test_a_plate_the_tracker_could_never_follow_is_kept():
    """The bug this closes: a real model detected a real number plate
    in nine frames out of ten and the app persisted nothing.

    Each sighting is its own track because the plate moves clean off
    its own last position between frames - which is exactly why the
    tracker could not follow it.
    """
    sightings = {1_000_000 + i: [_sighting(i, 200.0 + i * 320)] for i in range(4)}

    kept = drop_sightings_absorbed_by_tracks(sightings)

    assert len(kept) == 4


def test_the_unconfirmed_first_frame_of_a_followed_vehicle_is_absorbed():
    """Otherwise one vehicle is reported as two: a real track, and a
    one-frame ghost sitting right in front of it."""
    followed = [_sighting(2, 12.0, confirmed=True), _sighting(4, 14.0, confirmed=True)]
    ghost = [_sighting(0, 10.0)]

    kept = drop_sightings_absorbed_by_tracks({1_000_000: ghost, 0: followed})

    assert list(kept) == [0]


def test_a_sighting_somewhere_else_in_the_frame_is_not_absorbed():
    """Two vehicles is two vehicles, however close in time."""
    followed = [_sighting(2, 12.0, confirmed=True), _sighting(4, 14.0, confirmed=True)]
    elsewhere = [_sighting(0, 1500.0)]

    kept = drop_sightings_absorbed_by_tracks({1_000_000: elsewhere, 0: followed})

    assert len(kept) == 2


def test_absorbing_counts_processed_frames_not_indices():
    """Sampling at 5fps from a 10fps source numbers consecutive
    frames 0, 2, 4 - counting in indices missed every one of them."""
    followed = [_sighting(10, 12.0, confirmed=True), _sighting(15, 14.0, confirmed=True)]
    ghost = [_sighting(5, 10.0)]

    kept = drop_sightings_absorbed_by_tracks({1_000_000: ghost, 0: followed})

    assert list(kept) == [0]


def test_a_followed_track_is_never_dropped():
    followed = [_sighting(0, 10.0, confirmed=True)]

    kept = drop_sightings_absorbed_by_tracks({0: followed})

    assert list(kept) == [0]
