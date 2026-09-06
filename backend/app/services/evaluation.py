from collections import defaultdict

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.class_schema import get_class_schema
from app.db.models.annotation import Annotation
from app.db.models.frame_candidate import FrameCandidate
from app.db.models.ocr_candidate import OcrCandidate
from app.db.models.processing_run import ProcessingRun
from app.db.models.project import Project
from app.db.models.source import Source
from app.db.models.track import Track
from app.services.ocr_metrics import OcrAttempt, compute_ocr_agreement
from app.services.track_metrics import (
    TrackSummary,
    find_duplicate_track_pairs,
    find_fragmented_track_pairs,
)


def _build_track_summaries(db: Session, run_id: str) -> list[TrackSummary]:
    tracks = list(db.scalars(select(Track).where(Track.run_id == run_id)))
    summaries = []
    for track in tracks:
        frames = list(
            db.scalars(
                select(FrameCandidate).where(FrameCandidate.track_id == track.id).order_by(FrameCandidate.frame_index)
            )
        )
        if not frames:
            continue
        summaries.append(
            TrackSummary(
                track_id=track.id,
                start_ts=track.start_ts,
                end_ts=track.end_ts,
                first_bbox=tuple(frames[0].bbox_json),
                last_bbox=tuple(frames[-1].bbox_json),
            )
        )
    return summaries


def _compute_class_distribution(db: Session, run_id: str, class_schema_version: str) -> dict[str, int]:
    stmt = (
        select(Annotation.class_id, func.count(Annotation.id))
        .join(FrameCandidate, Annotation.frame_candidate_id == FrameCandidate.id)
        .join(Track, FrameCandidate.track_id == Track.id)
        .where(
            Track.run_id == run_id,
            Annotation.source == "human",
            Annotation.status.in_(["accepted", "hard"]),
            Annotation.class_id.is_not(None),
        )
        .group_by(Annotation.class_id)
    )
    schema_names = {c["id"]: c["name"] for c in get_class_schema(class_schema_version)}
    return {schema_names.get(class_id, str(class_id)): count for class_id, count in db.execute(stmt).all()}


def _compute_ocr_metrics(db: Session, run_id: str) -> dict:
    rows = list(
        db.execute(
            select(OcrCandidate.track_id, OcrCandidate.source, OcrCandidate.normalized_text, OcrCandidate.confidence, OcrCandidate.selected)
            .join(Track, OcrCandidate.track_id == Track.id)
            .where(Track.run_id == run_id)
        ).all()
    )
    attempts_by_track: dict[str, list[OcrAttempt]] = defaultdict(list)
    for track_id, source, normalized_text, confidence, selected in rows:
        attempts_by_track[track_id].append(OcrAttempt(source, normalized_text, confidence, selected))
    return compute_ocr_agreement(attempts_by_track)


def _build_failure_gallery(db: Session, run_id: str) -> list[dict]:
    tracks = list(
        db.scalars(select(Track).where(Track.run_id == run_id, (Track.bucket == "FAILED") | (Track.review_status == "failed")))
    )
    gallery = []
    for track in tracks:
        representative = db.scalar(
            select(FrameCandidate).where(FrameCandidate.track_id == track.id).order_by(FrameCandidate.frame_index).limit(1)
        )
        gallery.append(
            {
                "track_id": track.id,
                "bucket": track.bucket,
                "review_status": track.review_status,
                "representative_frame_id": representative.id if representative else None,
            }
        )
    return gallery


def evaluate_run(db: Session, run: ProcessingRun, source: Source, project: Project) -> dict:
    """Everything docs/02_IMPLEMENTATION_PLAN.md Phase 7 asks for, for
    one processing run. No implicit Definition of Done is given for
    this phase in the docs (unlike every other phase) - see
    docs/HANDOFF.md for the interpretation used here: every number
    below is honestly computable from data this project actually has,
    and anything that would require fabricated ground truth (e.g. true
    detection recall on a non-frozen source) reports ``null`` instead
    of a guess.
    """
    summaries = _build_track_summaries(db, run.id)
    all_tracks = list(db.scalars(select(Track).where(Track.run_id == run.id)))

    confirmed = [t for t in all_tracks if t.review_status in ("accepted", "hard")]
    failed = [t for t in all_tracks if t.review_status == "failed"]
    unreviewed = [t for t in all_tracks if t.review_status == "unreviewed"]

    recall = None
    if source.is_frozen and source.ground_truth_vehicle_count:
        recall = len(confirmed) / source.ground_truth_vehicle_count

    return {
        "run_id": run.id,
        "source_id": source.id,
        "is_frozen_validation_clip": source.is_frozen,
        "ground_truth_vehicle_count": source.ground_truth_vehicle_count,
        "track_counts": {
            "total": len(all_tracks),
            "confirmed": len(confirmed),
            "failed": len(failed),
            "unreviewed": len(unreviewed),
        },
        "detection_recall": recall,
        "duplicate_track_pairs": find_duplicate_track_pairs(summaries),
        "fragmented_track_pairs": find_fragmented_track_pairs(summaries),
        "class_distribution": _compute_class_distribution(db, run.id, project.class_schema_version),
        "ocr_metrics": _compute_ocr_metrics(db, run.id),
        "failure_gallery": _build_failure_gallery(db, run.id),
    }
