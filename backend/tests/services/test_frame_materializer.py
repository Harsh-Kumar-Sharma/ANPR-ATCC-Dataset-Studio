from pathlib import Path

import cv2
import numpy as np
import pytest

from app.db.models.source import Source
from app.db.session import SessionLocal
from app.services.frame_materializer import (
    FrameMaterializationError,
    get_or_create_frame,
    materialize_frames,
)
from tests.video_factory import create_synthetic_video


def _source_with_video(db, tmp_path: Path, name: str, frame_count: int = 20) -> Source:
    from app.db.models.project import Project

    video = create_synthetic_video(tmp_path / f"{name}.mp4", frame_count=frame_count, fps=10.0)
    project = Project(name=name, workspace_path=str(tmp_path / name))
    db.add(project)
    db.flush()
    source = Source(
        project_id=project.id,
        type="video",
        path_or_uri=str(video),
        fps=10.0,
        width=64,
        height=48,
        duration_ms=int(frame_count / 10.0 * 1000),
        frame_count=frame_count,
    )
    db.add(source)
    db.flush()
    return source


def test_get_or_create_frame_is_idempotent_per_source_and_index(tmp_path):
    db = SessionLocal()
    try:
        source = _source_with_video(db, tmp_path, "idempotent")
        first = get_or_create_frame(db, source.id, frame_index=5, timestamp_ms=500, width=64, height=48)
        second = get_or_create_frame(db, source.id, frame_index=5, timestamp_ms=500, width=64, height=48)
        assert first.id == second.id, "the same frame must not be recorded twice"
    finally:
        db.rollback()
        db.close()


def test_materialize_decodes_on_demand_and_reuses_the_cache(tmp_path):
    db = SessionLocal()
    try:
        source = _source_with_video(db, tmp_path, "cache")
        workspace = tmp_path / "cache-ws"
        frame = get_or_create_frame(db, source.id, frame_index=4, timestamp_ms=400, width=64, height=48)
        assert frame.image_path is None, "recording a frame must not write pixels"

        paths = materialize_frames(db, [frame], source, workspace)
        image_path = paths[frame.id]
        assert image_path.is_file()
        assert frame.image_path == str(image_path)

        # Second call must reuse the cached file rather than decoding again.
        marker = image_path.stat().st_mtime_ns
        again = materialize_frames(db, [frame], source, workspace)
        assert again[frame.id] == image_path
        assert image_path.stat().st_mtime_ns == marker, "cached frame was needlessly re-decoded"
    finally:
        db.rollback()
        db.close()


def test_materialize_recovers_when_the_cached_file_was_deleted(tmp_path):
    """A cached frame that disappears (workspace cleaned, file moved) must
    be re-decoded from the source, not treated as a hard failure - the
    pixels are always recoverable while the source video exists."""
    db = SessionLocal()
    try:
        source = _source_with_video(db, tmp_path, "deleted-cache")
        workspace = tmp_path / "deleted-cache-ws"
        frame = get_or_create_frame(db, source.id, frame_index=6, timestamp_ms=600, width=64, height=48)

        image_path = materialize_frames(db, [frame], source, workspace)[frame.id]
        image_path.unlink()
        assert frame.image_path is not None  # DB still points at the deleted file

        recovered = materialize_frames(db, [frame], source, workspace)[frame.id]
        assert recovered.is_file()
    finally:
        db.rollback()
        db.close()


def test_materialize_fails_clearly_when_the_source_video_is_gone(tmp_path):
    db = SessionLocal()
    try:
        source = _source_with_video(db, tmp_path, "missing-source")
        frame = get_or_create_frame(db, source.id, frame_index=3, timestamp_ms=300, width=64, height=48)
        Path(source.path_or_uri).unlink()

        with pytest.raises(FrameMaterializationError) as excinfo:
            materialize_frames(db, [frame], source, tmp_path / "missing-ws")
        assert "source video is missing" in str(excinfo.value)
    finally:
        db.rollback()
        db.close()


def test_materialized_frame_is_the_frame_that_was_asked_for(tmp_path):
    """Frame-accurate seeking is the assumption the whole full-frame
    export rests on: if a seek lands on a neighbouring frame, every
    exported label is silently misaligned with its image.

    The comparison is against a *sequential* decode of the same video
    rather than against the fill value the frame was painted with,
    because the lossy codec does not preserve exact colours - frame 7 of
    a synthetic clip decodes to roughly, not exactly, 7. Sequential
    decode is the ground truth for "which frame is at index N".
    """
    db = SessionLocal()
    try:
        source = _source_with_video(db, tmp_path, "seek", frame_count=30)
        workspace = tmp_path / "seek-ws"
        indices = (0, 7, 15, 23, 29)

        capture = cv2.VideoCapture(source.path_or_uri)
        sequential = []
        while True:
            ok, image = capture.read()
            if not ok:
                break
            sequential.append(image)
        capture.release()

        frames = [
            get_or_create_frame(db, source.id, frame_index=i, timestamp_ms=i * 100, width=64, height=48)
            for i in indices
        ]
        paths = materialize_frames(db, frames, source, workspace)

        for frame in frames:
            decoded = cv2.imread(str(paths[frame.id]))
            expected = sequential[frame.frame_index]
            difference = float(np.mean(np.abs(decoded.astype(int) - expected.astype(int))))
            assert difference < 5.0, (
                f"frame {frame.frame_index} differs from a sequential decode by {difference:.1f} - "
                "the seek landed on the wrong frame"
            )
    finally:
        db.rollback()
        db.close()


def test_materialize_records_true_image_dimensions(tmp_path):
    """Width/height on the frame row drive YOLO normalization, so they
    must come from the decoded pixels rather than being trusted from
    whatever the caller passed in."""
    db = SessionLocal()
    try:
        source = _source_with_video(db, tmp_path, "dims")
        frame = get_or_create_frame(db, source.id, frame_index=2, timestamp_ms=200, width=1, height=1)
        materialize_frames(db, [frame], source, tmp_path / "dims-ws")
        assert (frame.width, frame.height) == (64, 48)
    finally:
        db.rollback()
        db.close()
