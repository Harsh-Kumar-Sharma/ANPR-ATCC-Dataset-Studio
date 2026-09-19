"""How often the model's plate reading matched the human's.

What this used to measure was subtly different and much less useful. It
compared the model's top attempt against whichever ``ocr_candidates``
row was ``selected`` - but running OCR flags the model's own best
attempt as selected, so a track nobody had looked at compared the model
against itself and counted as an agreement. The rate was therefore
mostly a measure of how little reviewing had been done.

A human's reading now lives on the annotation (see
``services/plate_text.py``), so the comparison has two genuinely
different sides: what the model read, and what a person wrote down.
Tracks nobody has read are not counted at all, because nothing is known
about them.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelReading:
    """One plate the model read, and how sure it was."""

    normalized_text: str
    confidence: float


def compute_ocr_agreement(
    model_readings_by_track: dict[str, list[ModelReading]],
    human_plate_by_track: dict[str, str],
) -> dict:
    """Compare the model's best reading with the human's, per track.

    Counted only where both exist. A track the model could not read has
    nothing to be right or wrong about; a track no human has read has
    nothing to judge it against. Either way the honest answer is to
    leave it out rather than to assume the model was right, which is
    what the previous version did by default.

    There is still no independently verified plate ground truth here -
    only the review workflow's own record - and this metric does not
    pretend otherwise. A human who wrote down something different is
    real evidence the model was wrong; one who wrote down the same thing
    is real evidence it was right.
    """
    tracks_with_ocr = 0
    agreements = 0
    corrections = 0

    for track_id, readings in model_readings_by_track.items():
        human_plate = human_plate_by_track.get(track_id)
        if not readings or not human_plate:
            continue

        tracks_with_ocr += 1
        best = max(readings, key=lambda r: r.confidence)
        if best.normalized_text == human_plate:
            agreements += 1
        else:
            corrections += 1

    return {
        "tracks_with_ocr": tracks_with_ocr,
        "agreements": agreements,
        "corrections": corrections,
        "agreement_rate": (agreements / tracks_with_ocr) if tracks_with_ocr else None,
    }
