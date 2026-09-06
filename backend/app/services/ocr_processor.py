import cv2
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
from app.db.models.frame_candidate import FrameCandidate
from app.db.models.ocr_candidate import OcrCandidate
from app.db.models.track import Track
from app.ml.plate_ocr import PlateOcrEngine
from app.services.text_normalization import normalize_plate_text

#: A confidence at or above this stops trying further candidate
#: frames - "avoid OCR on every frame" per docs/03_TRD.md performance
#: guidance. Uncalibrated default, same caveat as the detector
#: confidence threshold and ranking thresholds.
DEFAULT_CONFIDENT_THRESHOLD = 0.7


def _ocr_candidate_frames_by_quality(db: Session, track_id: str) -> list[FrameCandidate]:
    frames = list(db.scalars(select(FrameCandidate).where(FrameCandidate.track_id == track_id)))
    shortlisted = [f for f in frames if f.flags_json and "ocr_candidate" in f.flags_json.get("roles", [])]
    shortlisted.sort(key=lambda f: f.flags_json.get("quality_score", 0.0), reverse=True)
    return shortlisted


def run_ocr_for_track(
    db: Session,
    track: Track,
    engine: PlateOcrEngine,
    confident_threshold: float = DEFAULT_CONFIDENT_THRESHOLD,
) -> list[OcrCandidate]:
    """Try OCR on the track's OCR-candidate frames (Phase 3's
    shortlist), best quality first. Stops once a confident reading is
    found rather than trying every frame - but every attempt actually
    made is persisted (docs/07_ML_CV_PIPELINE.md OCR step 6), and the
    best attempt is flagged as the track's selected result (step 7).
    """
    frames = _ocr_candidate_frames_by_quality(db, track.id)

    attempts: list[OcrCandidate] = []
    for frame in frames:
        image = cv2.imread(frame.image_path)
        if image is None:
            continue
        candidates = engine.read_plate(image)
        for candidate in candidates:
            row = OcrCandidate(
                track_id=track.id,
                frame_candidate_id=frame.id,
                source="model",
                plate_bbox_json=list(candidate.bbox_xyxy),
                text=candidate.text,
                normalized_text=normalize_plate_text(candidate.text),
                confidence=candidate.confidence,
            )
            db.add(row)
            attempts.append(row)

        if candidates and max(c.confidence for c in candidates) >= confident_threshold:
            break

    if attempts:
        best = max(attempts, key=lambda a: a.confidence)
        best.selected = True

    db.commit()
    for a in attempts:
        db.refresh(a)
    return attempts


def select_ocr_result(
    db: Session,
    track: Track,
    *,
    ocr_candidate_id: str | None,
    corrected_text: str | None,
    frame_candidate_id: str | None,
) -> OcrCandidate:
    """Human correction: pick an existing attempt, or add a new
    human-sourced one and pick that. Exactly one row per track is
    ``selected`` at a time."""
    db.query(OcrCandidate).filter(OcrCandidate.track_id == track.id).update({"selected": False})

    if ocr_candidate_id is not None:
        chosen = db.get(OcrCandidate, ocr_candidate_id)
        if chosen is None or chosen.track_id != track.id:
            raise NotFoundError(f"OCR candidate not found on this track: {ocr_candidate_id}")
        chosen.selected = True
    else:
        assert corrected_text is not None and frame_candidate_id is not None
        frame = db.get(FrameCandidate, frame_candidate_id)
        if frame is None or frame.track_id != track.id:
            raise NotFoundError(f"Frame candidate not found on this track: {frame_candidate_id}")
        chosen = OcrCandidate(
            track_id=track.id,
            frame_candidate_id=frame_candidate_id,
            source="human",
            plate_bbox_json=[0.0, 0.0, 0.0, 0.0],
            text=corrected_text,
            normalized_text=normalize_plate_text(corrected_text),
            confidence=1.0,
            selected=True,
        )
        db.add(chosen)

    db.commit()
    db.refresh(chosen)
    return chosen
