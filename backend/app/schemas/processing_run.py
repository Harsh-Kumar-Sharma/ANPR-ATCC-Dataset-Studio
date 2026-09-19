from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class SamplingConfig(BaseModel):
    target_fps: float = Field(gt=0)
    #: Walk the source at its own rate instead, keeping every frame
    #: the model found something in. ``target_fps`` is then ignored,
    #: and kept in the config so the request is still readable.
    every_frame: bool = False


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


class RunEstimateRead(BaseModel):
    """What a run would process, and what it would cost.

    Every number is an estimate. ``bytes_now`` is what the run writes
    while it runs; ``bytes_if_every_frame_reviewed`` is what it comes
    to if all of it is later opened for labelling, which is the number
    that actually fills a disk.
    """

    frames_to_process: int
    rows_expected: int
    bytes_now: int
    bytes_if_every_frame_reviewed: int
    free_bytes: int
    fits: bool
    reason: str | None = None
