from datetime import datetime

from pydantic import BaseModel, ConfigDict


class DetectionBoxRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    track_id: str
    tracker_track_id: int
    #: Full source-frame pixels, xyxy - the same space as the video's
    #: decoded frames, not the saved crop.
    bbox: list[float]
    detector_class: str
    confidence: float
    bucket: str | None
    review_status: str


class DetectionFrameRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    frame_index: int
    timestamp_ms: int
    boxes: list[DetectionBoxRead]


class DetectionRunRead(BaseModel):
    id: str
    status: str
    started_at: datetime
    track_count: int


class SourceDetectionsRead(BaseModel):
    source_id: str
    width: int
    height: int
    fps: float
    #: The run whose detections are in ``frames`` - null when the source
    #: has no completed run yet.
    run_id: str | None
    runs: list[DetectionRunRead]
    frames: list[DetectionFrameRead]
