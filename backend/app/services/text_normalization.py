import re

_NON_ALNUM = re.compile(r"[^A-Z0-9]")


def normalize_plate_text(raw_text: str) -> str:
    """Canonicalize raw OCR output for comparison/deduplication:
    uppercase, strip everything but letters and digits.

    Deliberately simple for V1 - no region-specific plate-format
    validation or character-confusion correction (e.g. O/0, I/1)
    without being asked for it; docs/07_ML_CV_PIPELINE.md only calls
    for "normalize candidate text", not format enforcement.
    """
    return _NON_ALNUM.sub("", raw_text.upper())
