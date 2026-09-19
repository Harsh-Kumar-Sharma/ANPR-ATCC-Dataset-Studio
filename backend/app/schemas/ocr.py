from datetime import datetime

from pydantic import BaseModel, ConfigDict


class OcrCandidateRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    track_id: str
    frame_candidate_id: str
    source: str
    plate_bbox_json: list[float]
    text: str
    normalized_text: str
    confidence: float
    selected: bool
    created_at: datetime


class PlateTextWrite(BaseModel):
    """What a human read off the plate. Empty clears it.

    Required rather than defaulted: with a default, a client that
    forgot the field would silently wipe a reading, and a malformed
    request would be indistinguishable from a deliberate clear.

    One field, because there is one thing to say. Choosing one of the
    model's readings is the client sending that reading's text - the
    server has no reason to know whether it was typed or clicked, and
    keeping a "which candidate did they pick" flag was how the reading
    ended up recorded in two places.
    """

    plate_text: str


class PlateReadingRead(BaseModel):
    """One plate the model read on a frame, and how sure it was."""

    text: str
    normalized_text: str
    confidence: float
