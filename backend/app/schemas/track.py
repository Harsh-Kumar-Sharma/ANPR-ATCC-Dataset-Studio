from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.schemas.processing_run import ProcessingRunRead


class FrameCandidateRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    track_id: str
    #: The full frame this detection sits on - what the labelling
    #: canvas works with. Null only for candidates from before full
    #: frames were captured.
    frame_id: str | None
    frame_index: int
    timestamp_ms: int
    image_path: str | None
    bbox_json: list[float]
    detector_class: str
    detector_confidence: float
    blur_score: float | None
    sharpness_score: float | None
    area_ratio: float | None
    flags_json: dict | None
    #: Whether the whole frame can be shown behind this detection.
    #: Review wants the vehicle the plate is on, not a 200x40 cut-out
    #: of the plate - but a live frame that was never written cannot
    #: be recovered, so the crop stays the fallback.
    full_frame: bool = False


class TrackRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    run_id: str
    tracker_track_id: int
    start_ts: int
    end_ts: int
    bucket: str | None
    review_status: str
    created_at: datetime


class TrackTimeline(BaseModel):
    """A track and its ordered frame candidates - the review UI's
    core unit per docs/06_UI_UX_SPEC.md ("review the vehicle track,
    not only an isolated image")."""

    track: TrackRead
    frames: list[FrameCandidateRead]


class ProcessingRunTracksResult(BaseModel):
    run: ProcessingRunRead
    tracks: list[TrackRead]


class TrackSweepRead(BaseModel):
    """What clearing out the unaccepted detections would take, or took."""

    kept: int
    deleted: int
    frames_deleted: int
    #: Held back because a dataset version already names them.
    held: int
    bytes_freed: int
