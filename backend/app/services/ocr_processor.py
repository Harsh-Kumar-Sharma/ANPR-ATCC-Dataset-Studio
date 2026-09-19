from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.frame_candidate import FrameCandidate
from app.db.models.ocr_candidate import OcrCandidate
from app.db.models.track import Track
from app.core.errors import AppError
from app.ml.plate_ocr import PlateOcrEngine
from app.services.crops import crop_array
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
        # Through the crop service rather than straight off disk: an
        # offline run writes no crop file, the pixels are cut out of
        # the source video on demand.
        try:
            image = crop_array(db, frame)
        except AppError:
            continue
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
        # The model's own best reading, flagged for the UI to lead with.
        # It is not a human decision and never was: a human's reading
        # goes on the annotation (see services/plate_text.py), so this
        # flag means one thing and nothing writes it but this function.
        #
        # Earlier runs are cleared first. Frames and tracks outlive a
        # run, so a second OCR pass used to leave two rows flagged best
        # on one track - which the model's own docstring forbids, and
        # which nothing was left to fix once human selection went away.
        db.query(OcrCandidate).filter(OcrCandidate.track_id == track.id).update({"selected": False})
        best = max(attempts, key=lambda a: a.confidence)
        best.selected = True

    db.commit()
    for a in attempts:
        db.refresh(a)
    return attempts
