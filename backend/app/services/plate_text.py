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
from app.services.annotation_attributes import clean_attributes
from app.services.review import get_human_annotation


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
    annotation = get_human_annotation(db, track.id)
    if annotation is None:
        raise NoLabelYetError(
            "This track has no label to put a plate on yet. Review it first - accept it, or flag it hard."
        )

    attributes = dict(annotation.attributes or {})
    attributes["plate_text"] = plate_text
    # Not wrapped: InvalidAttributeError is already an AppError carrying
    # a 400 and a message naming the attribute, and there is no context
    # to add here - unlike frames._validate, which adds which box.
    annotation.attributes = clean_attributes(attributes, stored=annotation.attributes or {})

    db.flush()
    return annotation


@dataclass
class PlateReading:
    """One plate the model read on a frame, and how sure it was."""

    text: str
    normalized_text: str
    confidence: float


def plate_readings_for_frame(db: Session, frame_id: str) -> list[PlateReading]:
    """What the model read on this frame, most confident first.

    By frame rather than by track, because the labelling canvas has no
    track - it holds a picture. The readings come from the detections on
    that frame, so a labeller can see what the model made of each plate
    instead of retyping one it already has.

    Every reading on the frame is offered rather than only the ones
    overlapping the selected box. Matching them to a box would mean a
    second copy of the association threshold that ``active_learning``
    owns, and a handful of lines is a list a person can simply read.

    ``source == "model"`` is not decoration. The migration leaves a
    human row behind when it has nowhere safe to put it, and those were
    written with confidence 1.0 - so without this filter somebody's own
    typing would sort to the top of a list headed "Model read:".

    Deduplicated by reading, because frames outlive runs: re-running
    detection and OCR piles more rows onto the same frame, and the same
    plate read four times is one suggestion, not four.
    """
    rows = db.scalars(
        select(OcrCandidate)
        .join(FrameCandidate, OcrCandidate.frame_candidate_id == FrameCandidate.id)
        .where(FrameCandidate.frame_id == frame_id, OcrCandidate.source == "model")
        .order_by(OcrCandidate.confidence.desc(), OcrCandidate.id)
    )

    best: dict[str, PlateReading] = {}
    for row in rows:
        if not row.normalized_text:
            # The model read something that canonicalises to nothing.
            # Offering it gave the canvas a "(no text)" button that
            # clears the plate when pressed, which is not a suggestion.
            continue
        # Ordered most confident first, so the first of a repeated
        # reading is the one worth keeping.
        best.setdefault(
            row.normalized_text,
            PlateReading(text=row.text, normalized_text=row.normalized_text, confidence=row.confidence),
        )
    return list(best.values())
