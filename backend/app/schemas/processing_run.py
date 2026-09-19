from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class SamplingConfig(BaseModel):
    target_fps: float = Field(gt=0)


class ProcessingRunCreate(BaseModel):
    sampling_config: SamplingConfig
    #: Which model detects. Omitted means the default, so every caller
    #: written before there was a choice keeps working.
    model_id: str | None = None


class SampledFrameRead(BaseModel):
    frame_index: int
    timestamp_ms: int


class ProcessingRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    source_id: str
    detector_version: str | None
    tracker_config: dict | None
    sampling_config: dict
    status: str
    sampled_frame_count: int | None
    error_message: str | None
    started_at: datetime
    completed_at: datetime | None


class ProcessingRunResult(BaseModel):
    run: ProcessingRunRead
    sampled_frames: list[SampledFrameRead]
