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
from app.services.frame_ranking import (
    DEFAULT_RANKING_CONFIG,
    RankingConfig,
    classify_bucket,
    compute_composite_score,
    compute_temporal_stability,
    select_roles_by_score,
)
from app.services.frame_sampler import decode_sampled_frames, sample_frame_timestamps
from app.services.quality_signals import compute_area_ratio, compute_sharpness, is_truncated, sharpness_to_blur_score


@dataclass
class _Observation:
    frame_index: int
    timestamp_ms: int
    bbox: tuple[float, float, float, float]
    class_id: int
    confidence: float
    crop: np.ndarray
    sharpness_score: float
    blur_score: float
    area_ratio: float
    truncated: bool


ObservationsByTrack = dict[int, list[_Observation]]


def observe_frame(
    observations_by_track: ObservationsByTrack,
    image: np.ndarray,
    frame_index: int,
    timestamp_ms: int,
    detector: Detector,
    tracker: Tracker,
) -> None:
    """Run detect -> track on one frame and accumulate its
    observations by track id, in place.

    Shared by offline processing (``process_source`` below) and live
    RTSP capture (``rtsp_processor.py``) - both paths must produce
    identical downstream data, per docs/02_IMPLEMENTATION_PLAN.md
    Phase 9: "same downstream track/review contracts". Nothing here
    knows or cares whether ``image`` came from a decoded file frame or
    a live capture read.
    """
    frame_height, frame_width = image.shape[:2]
    detections = detector.detect(image)
    for tracked in tracker.update(detections, timestamp_ms=timestamp_ms):
        crop = _crop(image, tracked.bbox_xyxy)
        if crop is None:
            continue
        sharpness = compute_sharpness(crop)
        observations_by_track.setdefault(tracked.track_id, []).append(
            _Observation(
                frame_index=frame_index,
                timestamp_ms=timestamp_ms,
                bbox=tracked.bbox_xyxy,
                class_id=tracked.class_id,
                confidence=tracked.confidence,
                crop=crop,
                sharpness_score=sharpness,
                blur_score=sharpness_to_blur_score(sharpness),
                area_ratio=compute_area_ratio(tracked.bbox_xyxy, frame_width, frame_height),
                truncated=is_truncated(tracked.bbox_xyxy, frame_width, frame_height),
            )
        )


def persist_observations(
    db: Session,
    run: ProcessingRun,
    observations_by_track: ObservationsByTrack,
    tracks_root: Path,
    class_names: dict[int, str],
    ranking_config: RankingConfig = DEFAULT_RANKING_CONFIG,
) -> list[Track]:
    """Rank and persist every accumulated track - the same Phase 2/3
    logic regardless of whether the observations came from a finite
    offline pass or a live capture session."""
    tracks = []
    for tracker_track_id, observations in observations_by_track.items():
        observations.sort(key=lambda o: o.frame_index)
        tracks.append(_persist_track(db, run, tracker_track_id, observations, tracks_root, class_names, ranking_config))
    return tracks


def process_source(
    db: Session,
    source: Source,
    workspace_path: Path,
    run: ProcessingRun,
    target_fps: float,
    detector: Detector,
    tracker: Tracker,
    ranking_config: RankingConfig = DEFAULT_RANKING_CONFIG,
) -> ProcessingRun:
    """Sample, detect and track vehicles across an offline source
    video, then persist the resulting tracks, their frame candidates,
    and (Phase 3) each closed track's representative-frame ranking and
    hard/failed classification.

    Never drops an observation because of low quality - every tracked
    detection becomes a frame_candidates row regardless of how it
    ranks; HARD/FAILED tracks are labeled, not discarded.
    """
    sampled = sample_frame_timestamps(frame_count=source.frame_count, native_fps=source.fps, target_fps=target_fps)

    observations_by_track: ObservationsByTrack = {}
    for sampled_frame, image in decode_sampled_frames(Path(source.path_or_uri), sampled):
        observe_frame(observations_by_track, image, sampled_frame.frame_index, sampled_frame.timestamp_ms, detector, tracker)

    tracks_root = workspace_path / "derived" / "tracks"
    persist_observations(db, run, observations_by_track, tracks_root, detector.class_names, ranking_config)

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
    ranking_config: RankingConfig,
) -> Track:
    confidences = [o.confidence for o in observations]
    composite_scores = [
        compute_composite_score(
            blur_score=o.blur_score,
            area_ratio=o.area_ratio,
            confidence=o.confidence,
            temporal_stability=compute_temporal_stability(confidences, i),
            truncated=o.truncated,
            config=ranking_config,
        )
        for i, o in enumerate(observations)
    ]
    roles_by_frame = select_roles_by_score(composite_scores, ranking_config)
    bucket = classify_bucket(max(composite_scores), ranking_config)

    track = Track(
        run_id=run.id,
        tracker_track_id=tracker_track_id,
        start_ts=observations[0].timestamp_ms,
        end_ts=observations[-1].timestamp_ms,
        bucket=bucket,
    )
    db.add(track)
    db.flush()  # assign track.id

    track_dir = tracks_root / track.id
    track_dir.mkdir(parents=True, exist_ok=True)

    for obs, score, roles in zip(observations, composite_scores, roles_by_frame):
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
                blur_score=obs.blur_score,
                sharpness_score=obs.sharpness_score,
                area_ratio=obs.area_ratio,
                flags_json={
                    "truncated": obs.truncated,
                    "quality_score": score,
                    "roles": sorted(roles),
                },
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
