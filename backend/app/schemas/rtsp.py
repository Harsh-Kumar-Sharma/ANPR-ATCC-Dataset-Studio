from pydantic import BaseModel, Field

from app.schemas.processing_run import ProcessingRunRead
from app.schemas.source import SourceRead


class RtspStartRequest(BaseModel):
    rtsp_url: str
    #: RTSP cameras often don't report a reliable FPS the way a file's
    #: container metadata does - this is a hint used for the tracker's
    #: lost-track-buffer sizing, not a measured value.
    expected_fps: float = Field(default=10.0, gt=0)
    buffer_maxlen: int = Field(default=300, gt=0)
    #: Which model detects on the live stream. A live session is where
    #: a model you trained earns its keep, so this is not the one place
    #: that stays hard-coded.
    model_id: str | None = None


class RtspStartResult(BaseModel):
    source: SourceRead
    run: ProcessingRunRead


class RtspSessionStatusRead(BaseModel):
    run_id: str
    connected: bool
    reconnect_attempts: int
    frames_captured: int
    frames_dropped: int
    tracks_persisted: int
    stopped: bool
    error: str | None
