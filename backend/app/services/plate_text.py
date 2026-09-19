"""Where a human's plate reading lives: on the annotation, and nowhere else.

It used to live in two places. Typing a correction in track review added
a human-sourced row to ``ocr_candidates``; typing one on the labelling
canvas put it in the annotation's attributes. The two stored different
forms of the same string, only the second reached an exported dataset,
and the OCR agreement metric counted the first as evidence about the
model when it was really evidence about the reviewer.

``ocr_candidates`` is the model's record now - what it read, how sure it
was, and which of its own attempts was best. A human's reading is a
property of the vehicle in the box, which is what an annotation is for.
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ConflictError
from app.db.models.annotation import Annotation
from app.db.models.frame_candidate import FrameCandidate
from app.db.models.ocr_candidate import OcrCandidate
from app.db.models.track import Track
from app.services.annotation_attributes import InvalidAttributeError, clean_attributes


class NoLabelYetError(ConflictError):
    """A plate with nowhere to go.

    There is no annotation until a track has been reviewed, and this is
    the one case the old design handled by writing the reading into a
    table nothing exported - which looked like it worked and lost the
    work. Saying so is the honest alternative.
    """

    code = "no_label_yet"


def set_track_plate_text(db: Session, track: Track, plate_text: str) -> Annotation:
    """Record what a human read off this track's plate.

    Canonicalised and checked by the same validator the labelling canvas
    uses, so the two cannot disagree about what a plate may be. Empty
    clears it, the way clearing any attribute does.

    Only ``plate_text`` is touched. The class, the box and every other
    attribute on the annotation are none of this function's business.
    """
    annotation = _human_annotation(db, track.id)
    if annotation is None:
        raise NoLabelYetError(
            "This track has no label to put a plate on yet. Review it first - accept it, or flag it hard."
        )

    attributes = dict(annotation.attributes or {})
    attributes["plate_text"] = plate_text
    try:
        annotation.attributes = clean_attributes(attributes, stored=annotation.attributes or {})
    except InvalidAttributeError as e:
        raise InvalidAttributeError(str(e)) from e

    db.flush()
    return annotation


def _human_annotation(db: Session, track_id: str) -> Annotation | None:
    return db.scalar(
        select(Annotation)
        .join(FrameCandidate, Annotation.frame_candidate_id == FrameCandidate.id)
        .where(FrameCandidate.track_id == track_id, Annotation.source == "human")
    )


@dataclass
class PlateReading:
    """One plate the model read, and the vehicle it read it from.

    ``bbox_json`` is the *detection's* box in full-frame pixels, not the
    plate's. The plate box is relative to a cropped vehicle image, which
    is meaningless to a canvas drawing on the frame - and the question a
    labeller is asking is "which vehicle is this reading about", which
    the detection answers.
    """

    frame_candidate_id: str
    text: str
    normalized_text: str
    confidence: float
    bbox_json: list[float]


def plate_readings_for_frame(db: Session, frame_id: str) -> list[PlateReading]:
    """What the model read on this frame, most confident first.

    By frame rather than by track, because the labelling canvas has no
    track - it holds a picture. The readings come from the detections on
    that frame, so a labeller can see what the model made of each plate
    instead of retyping one it already has.

    Every reading on the frame is offered rather than only the ones
    overlapping the selected box. Matching them to a box would mean a
    second copy of the association threshold that ``active_learning``
    owns, and on a frame with three vehicles the list is three lines a
    person can simply read.
    """
    rows = db.execute(
        select(OcrCandidate, FrameCandidate.bbox_json)
        .join(FrameCandidate, OcrCandidate.frame_candidate_id == FrameCandidate.id)
        .where(FrameCandidate.frame_id == frame_id)
        .order_by(OcrCandidate.confidence.desc(), OcrCandidate.id)
    ).all()
    return [
        PlateReading(
            frame_candidate_id=reading.frame_candidate_id,
            text=reading.text,
            normalized_text=reading.normalized_text,
            confidence=reading.confidence,
            bbox_json=list(bbox),
        )
        for reading, bbox in rows
    ]
