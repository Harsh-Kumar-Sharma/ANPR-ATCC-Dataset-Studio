from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

from app.schemas.track import TrackRead

ReviewDecision = Literal["accepted", "hard", "failed"]


class TrackReviewRequest(BaseModel):
    frame_candidate_id: str
    decision: ReviewDecision
    class_id: int | None = None
    bbox_json: list[float] | None = None

    @model_validator(mode="after")
    def _class_required_unless_failed(self) -> "TrackReviewRequest":
        if self.decision in ("accepted", "hard") and self.class_id is None:
            raise ValueError("class_id is required for an 'accepted' or 'hard' decision")
        if self.bbox_json is not None and len(self.bbox_json) != 4:
            raise ValueError("bbox_json must have exactly 4 values [x1, y1, x2, y2]")
        return self


class AnnotationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    frame_id: str
    #: Present only for labels written through track review.
    frame_candidate_id: str | None
    source: str
    class_id: int | None
    bbox_json: list[float]
    attributes: dict
    status: str
    updated_at: datetime


class TrackReviewResult(BaseModel):
    track: TrackRead
    annotation: AnnotationRead
