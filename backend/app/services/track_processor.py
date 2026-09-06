from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from sqlalchemy.orm import Session

from app.db.models.frame_candidate import FrameCandidate
from app.db.models.processing_run import ProcessingRun
from app.db.models.source import Source
from app.db.models.track import Track
from app.ml.detector import Detector
from app.ml.tracker import Tracker
from app.services.frame_sampler import decode_sampled_frames, sample_frame_timestamps


@dataclass
class _Observation:
    frame_index: int
    timestamp_ms: int
    bbox: tuple[float, float, float, float]
    class_id: int
    confidence: float
    crop: np.ndarray


def process_source(
    db: Session,
    source: Source,
    workspace_path: Path,
    run: ProcessingRun,
    target_fps: float,
    detector: Detector,
    tracker: Tracker,
) -> ProcessingRun:
    """Sample, detect and track vehicles across a source video, then
    persist the resulting tracks and their frame candidates.

    Never overwrites or drops an observation because of low quality -
    that judgment belongs to Phase 3's ranking, not here. Every
    tracked detection becomes a frame_candidates row.
    """
    sampled = sample_frame_timestamps(frame_count=source.frame_count, native_fps=source.fps, target_fps=target_fps)

    observations_by_track: dict[int, list[_Observation]] = {}

    for sampled_frame, image in decode_sampled_frames(Path(source.path_or_uri), sampled):
        detections = detector.detect(image)
        for tracked in tracker.update(detections, timestamp_ms=sampled_frame.timestamp_ms):
            crop = _crop(image, tracked.bbox_xyxy)
            if crop is None:
                continue
            observations_by_track.setdefault(tracked.track_id, []).append(
                _Observation(
                    frame_index=sampled_frame.frame_index,
                    timestamp_ms=sampled_frame.timestamp_ms,
                    bbox=tracked.bbox_xyxy,
                    class_id=tracked.class_id,
                    confidence=tracked.confidence,
                    crop=crop,
                )
            )

    tracks_root = workspace_path / "derived" / "tracks"
    for tracker_track_id, observations in observations_by_track.items():
        observations.sort(key=lambda o: o.frame_index)
        _persist_track(db, run, tracker_track_id, observations, tracks_root, detector.class_names)

    run.status = "completed"
    run.sampled_frame_count = len(sampled)
    db.commit()
    db.refresh(run)
    return run


def _persist_track(
    db: Session,
    run: ProcessingRun,
    tracker_track_id: int,
    observations: list[_Observation],
    tracks_root: Path,
    class_names: dict[int, str],
) -> Track:
    track = Track(
        run_id=run.id,
        tracker_track_id=tracker_track_id,
        start_ts=observations[0].timestamp_ms,
        end_ts=observations[-1].timestamp_ms,
    )
    db.add(track)
    db.flush()  # assign track.id

    track_dir = tracks_root / track.id
    track_dir.mkdir(parents=True, exist_ok=True)

    for obs in observations:
        image_path = track_dir / f"frame_{obs.frame_index:06d}.jpg"
        cv2.imwrite(str(image_path), obs.crop)
        db.add(
            FrameCandidate(
                track_id=track.id,
                frame_index=obs.frame_index,
                timestamp_ms=obs.timestamp_ms,
                image_path=str(image_path),
                bbox_json=list(obs.bbox),
                detector_class=class_names.get(obs.class_id, str(obs.class_id)),
                detector_confidence=obs.confidence,
            )
        )

    return track


def _crop(image: np.ndarray, bbox_xyxy: tuple[float, float, float, float]) -> np.ndarray | None:
    height, width = image.shape[:2]
    x1, y1, x2, y2 = (int(round(v)) for v in bbox_xyxy)
    x1, x2 = max(0, min(x1, width)), max(0, min(x2, width))
    y1, y2 = max(0, min(y1, height)), max(0, min(y2, height))
    if x2 <= x1 or y2 <= y1:
        return None
    return image[y1:y2, x1:x2].copy()
