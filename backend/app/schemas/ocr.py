from datetime import datetime

from pydantic import BaseModel, ConfigDict, model_validator


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


class OcrSelectionRequest(BaseModel):
    ocr_candidate_id: str | None = None
    corrected_text: str | None = None
    frame_candidate_id: str | None = None

    @model_validator(mode="after")
    def _validate(self) -> "OcrSelectionRequest":
        if (self.ocr_candidate_id is None) == (self.corrected_text is None):
            raise ValueError("Provide exactly one of ocr_candidate_id or corrected_text")
        if self.corrected_text is not None and self.frame_candidate_id is None:
            raise ValueError("frame_candidate_id is required when providing corrected_text")
        return self
