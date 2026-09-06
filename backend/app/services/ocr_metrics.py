from dataclasses import dataclass


@dataclass(frozen=True)
class OcrAttempt:
    source: str  # "model" | "human"
    normalized_text: str
    confidence: float
    selected: bool


def compute_ocr_agreement(attempts_by_track: dict[str, list[OcrAttempt]]) -> dict:
    """How often the model's own top attempt matches what was
    ultimately selected as truth (docs/07_ML_CV_PIPELINE.md OCR step
    7-8: "select track-level OCR" + "allow human correction").

    This is an honest metric this codebase can actually compute -
    there is no independently-verified plate ground truth, only the
    review workflow's own record of what a human accepted or changed.
    A track where the human corrected the text is real evidence the
    model was wrong; a track where a model attempt stayed selected
    (or the human re-picked the exact same text) is evidence it was
    right.
    """
    tracks_with_ocr = 0
    agreements = 0
    corrections = 0

    for attempts in attempts_by_track.values():
        model_attempts = [a for a in attempts if a.source == "model"]
        selected = next((a for a in attempts if a.selected), None)
        if not model_attempts or selected is None:
            continue

        tracks_with_ocr += 1
        top_model = max(model_attempts, key=lambda a: a.confidence)

        if top_model.normalized_text == selected.normalized_text:
            agreements += 1
        else:
            corrections += 1

    return {
        "tracks_with_ocr": tracks_with_ocr,
        "agreements": agreements,
        "corrections": corrections,
        "agreement_rate": (agreements / tracks_with_ocr) if tracks_with_ocr else None,
    }
