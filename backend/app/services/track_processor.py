from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from sqlalchemy.orm import Session

from app.db.models.frame import Frame
from app.db.models.frame_candidate import FrameCandidate
from app.db.models.processing_run import ProcessingRun
from app.db.models.source import Source
from app.db.models.track import Track
from app.ml.detector import Detector
from app.ml.tracker import Tracker
from app.services.frame_materializer import get_or_create_frame
from app.services.frame_ranking import (
    DEFAULT_RANKING_CONFIG,
    RankingConfig,
    classify_bucket,
    compute_composite_score,
    compute_temporal_stability,
    select_roles_by_score,
)
from app.services.frame_sampler import decode_sampled_frames, sample_frame_timestamps
from app.services.sighting_linker import link_single_sightings
from app.services.track_metrics import DEFAULT_FRAGMENTATION_IOU_THRESHOLD, iou
from app.services.quality_signals import compute_area_ratio, compute_sharpness, is_truncated, sharpness_to_blur_score
from app.services.run_estimate import STOP_BELOW_BYTES, free_bytes_for


#: Called with (fraction, message). Reporting is best-effort telemetry:
#: it must never change what gets persisted.
ProgressReporter = Callable[[float, str], None]

#: Detection dominates the wall clock, so it owns most of the bar. The
#: remainder covers persisting tracks and their crops.
DETECTION_SHARE_OF_PROGRESS = 0.9

#: Frequent enough to look live, rare enough not to make the progress
#: file the bottleneck on a fast GPU.
PROGRESS_REPORT_EVERY_FRAMES = 10

#: How often the run looks at how much disk is left. A stat call is
#: cheap but not free, and space does not disappear between frames.
SPACE_CHECK_EVERY_FRAMES = 500


@dataclass
class _Observation:
    frame_index: int
    timestamp_ms: int
    bbox: tuple[float, float, float, float]
    class_id: int
    confidence: float
    #: False when the tracker never managed to follow this detection
    #: across frames. Still a real observation - see
    #: ``ByteTrackTracker`` - but it stands alone.
    confirmed: bool
    #: The pixels, and only when they cannot be recovered - see
    #: ``observe_frame``'s ``keep_crop``. Held in memory until the
    #: track is persisted, so an offline run leaves it None: at two
    #: vehicles a frame over 90,003 frames it is gigabytes of RAM
    #: before it is gigabytes of JPEG.
    crop: np.ndarray | None
    sharpness_score: float
    blur_score: float
    area_ratio: float
    truncated: bool
    # Required, not defaulted: these become the frame row's dimensions,
    # which YOLO normalization divides by at export time.
    frame_width: int
    frame_height: int


ObservationsByTrack = dict[int, list[_Observation]]


def observe_frame(
    observations_by_track: ObservationsByTrack,
    image: np.ndarray,
    frame_index: int,
    timestamp_ms: int,
    detector: Detector,
    tracker: Tracker,
    keep_crop: bool = False,
) -> list[tuple[int, _Observation]]:
    """Run detect -> track on one frame and accumulate its
    observations by track id, in place.

    Shared by offline processing (``process_source`` below) and live
    RTSP capture (``rtsp_processor.py``) - both paths must produce
    identical downstream data, per docs/02_IMPLEMENTATION_PLAN.md
    Phase 9: "same downstream track/review contracts". Nothing here
    knows or cares whether ``image`` came from a decoded file frame or
    a live capture read.

    Also returns this frame's own ``(track_id, observation)`` pairs, so a
    live caller can draw exactly what was tracked on this frame without
    searching the accumulated history.
    """
    frame_height, frame_width = image.shape[:2]
    detections = detector.detect(image)
    tracked_on_frame: list[tuple[int, _Observation]] = []
    for tracked in tracker.update(detections, timestamp_ms=timestamp_ms):
        crop = _crop(image, tracked.bbox_xyxy)
        if crop is None:
            continue
        sharpness = compute_sharpness(crop)
        observation = _Observation(
            frame_index=frame_index,
            timestamp_ms=timestamp_ms,
            bbox=tracked.bbox_xyxy,
            class_id=tracked.class_id,
            confidence=tracked.confidence,
            confirmed=tracked.confirmed,
            crop=crop if keep_crop else None,
            sharpness_score=sharpness,
            blur_score=sharpness_to_blur_score(sharpness),
            area_ratio=compute_area_ratio(tracked.bbox_xyxy, frame_width, frame_height),
            truncated=is_truncated(tracked.bbox_xyxy, frame_width, frame_height),
            frame_width=frame_width,
            frame_height=frame_height,
        )
        observations_by_track.setdefault(tracked.track_id, []).append(observation)
        tracked_on_frame.append((tracked.track_id, observation))
    return tracked_on_frame


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
    observations_by_track = drop_sightings_absorbed_by_tracks(observations_by_track)
    # Then join what is left of one vehicle into one track. A plate
    # the tracker could never follow arrives as a sighting per
    # frame, and seventeen entries for one car going past is not a
    # review queue.
    observations_by_track = link_single_sightings(observations_by_track)

    tracks = []
    # Frames are shared across tracks (two vehicles in one frame are one
    # frame), so resolve each one once per call rather than per track.
    frames_by_index: dict[int, Frame] = {}
    for tracker_track_id, observations in observations_by_track.items():
        observations.sort(key=lambda o: o.frame_index)
        tracks.append(
            _persist_track(
                db, run, tracker_track_id, observations, tracks_root, class_names, ranking_config, frames_by_index
            )
        )
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
    on_progress: ProgressReporter | None = None,
    every_frame: bool = False,
) -> ProcessingRun:
    """Sample, detect and track vehicles across an offline source
    video, then persist the resulting tracks, their frame candidates,
    and (Phase 3) each closed track's representative-frame ranking and
    hard/failed classification.

    Never drops an observation because of low quality - every tracked
    detection becomes a frame_candidates row regardless of how it
    ranks; HARD/FAILED tracks are labeled, not discarded.

    ``every_frame`` walks the source at its own rate instead of
    sampling, keeping every frame the model found something in. That
    is the mode for building a dataset out of a whole clip rather than
    a survey of it.

    ``on_progress`` is optional and reports decode/detect progress as a
    0.0-1.0 fraction. Persisting is deliberately reported as a single
    step near the end rather than interleaved: it is fast relative to
    detection, and a bar that stalls at 90% for a second is more honest
    than one that claims finer resolution than it has.
    """
    rate = source.fps if every_frame else target_fps
    sampled = sample_frame_timestamps(frame_count=source.frame_count, native_fps=source.fps, target_fps=rate)

    observations_by_track: ObservationsByTrack = {}
    frames_kept: set[int] = set()
    stopped_early = None
    total = len(sampled)
    for processed, (sampled_frame, image) in enumerate(decode_sampled_frames(Path(source.path_or_uri), sampled), start=1):
        found = observe_frame(
            observations_by_track, image, sampled_frame.frame_index, sampled_frame.timestamp_ms, detector, tracker
        )
        if found:
            frames_kept.add(sampled_frame.frame_index)

        # Stopped, not crashed. A run that fills the disk takes the
        # rest of the app down with it, and the frames already found
        # are real observations worth keeping.
        if processed % SPACE_CHECK_EVERY_FRAMES == 0:
            free = free_bytes_for(workspace_path)
            if free < STOP_BELOW_BYTES:
                stopped_early = (
                    f"Stopped at frame {processed} of {total}: only {free // 1024**2} MB left on disk. "
                    f"The {len(frames_kept)} frames found so far are kept."
                )
                break

        if on_progress is not None and total and (processed % PROGRESS_REPORT_EVERY_FRAMES == 0 or processed == total):
            on_progress(
                DETECTION_SHARE_OF_PROGRESS * processed / total,
                f"Frame {processed} of {total} · {len(frames_kept)} kept",
            )

    if on_progress is not None:
        on_progress(DETECTION_SHARE_OF_PROGRESS, "Saving tracks")

    tracks_root = workspace_path / "derived" / "tracks"
    persist_observations(db, run, observations_by_track, tracks_root, detector.class_names, ranking_config)

    run.status = "completed"
    # What it actually looked at, which is not what it planned to look
    # at if it ran out of disk on the way.
    run.sampled_frame_count = len(sampled) if stopped_early is None else processed
    if stopped_early is not None:
        run.error_message = stopped_early
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
    frames_by_index: dict[int, Frame],
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

    # Made only if something is going to be put in it. A run over an
    # offline video writes no crops at all, so it leaves no directory.
    track_dir = tracks_root / track.id
    if any(o.crop is not None for o in observations):
        track_dir.mkdir(parents=True, exist_ok=True)

    for obs, score, roles in zip(observations, composite_scores, roles_by_frame):
        image_path = None
        if obs.crop is not None:
            image_path = track_dir / f"frame_{obs.frame_index:06d}.jpg"
            cv2.imwrite(str(image_path), obs.crop)

        frame = frames_by_index.get(obs.frame_index)
        if frame is None:
            frame = get_or_create_frame(
                db,
                source_id=run.source_id,
                frame_index=obs.frame_index,
                timestamp_ms=obs.timestamp_ms,
                width=obs.frame_width,
                height=obs.frame_height,
            )
            frames_by_index[obs.frame_index] = frame

        db.add(
            FrameCandidate(
                track_id=track.id,
                frame_id=frame.id,
                frame_index=obs.frame_index,
                timestamp_ms=obs.timestamp_ms,
                image_path=str(image_path) if image_path is not None else None,
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


#: How close a single sighting has to be to a followed track's first
#: box to be considered the same vehicle. The same threshold the
#: fragmentation check uses, for the same reason: it is the number
#: this project already means by "these two boxes are the same thing".
ABSORB_IOU_THRESHOLD = DEFAULT_FRAGMENTATION_IOU_THRESHOLD


def drop_sightings_absorbed_by_tracks(observations_by_track: ObservationsByTrack) -> ObservationsByTrack:
    """Remove single sightings that were the start of a followed track.

    ByteTrack confirms a track only on the second consecutive match,
    so the first frame of every followed vehicle arrives unconfirmed.
    Keeping it as its own track would report one vehicle as two - a
    real track plus a one-frame ghost sitting right in front of it.

    A sighting is dropped only when a followed track begins on the
    very next processed frame, in the same place. "Next processed"
    rather than "next index": sampling at 5fps from a 10fps source
    numbers consecutive frames 0, 2, 4, so counting in indices would
    have missed every one of them.

    That pairing is what makes this safe for the case single
    sightings exist for. A number plate at 7fps has moved clean off
    its own last position by the next frame, so it overlaps nothing
    and is kept.
    """
    processed = sorted({o.frame_index for observations in observations_by_track.values() for o in observations})
    if not processed:
        return observations_by_track

    next_processed = {earlier: later for earlier, later in zip(processed, processed[1:])}
    starts: dict[int, list[tuple[float, float, float, float]]] = {}
    for observations in observations_by_track.values():
        if any(o.confirmed for o in observations):
            starts.setdefault(observations[0].frame_index, []).append(observations[0].bbox)

    kept: ObservationsByTrack = {}
    for track_id, observations in observations_by_track.items():
        if len(observations) == 1 and not observations[0].confirmed:
            sighting = observations[0]
            following = next_processed.get(sighting.frame_index)
            if following is not None and any(
                iou(sighting.bbox, start) >= ABSORB_IOU_THRESHOLD for start in starts.get(following, [])
            ):
                continue
        kept[track_id] = observations
    return kept
