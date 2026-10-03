from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

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
    #: Keep the captured frames themselves, so there is something to
    #: label even when detection finds nothing.
    keep_frames: bool = False
    #: Keep one frame in this many. Consecutive frames mostly show the
    #: same thing.
    keep_every: int = Field(default=10, ge=1)
    #: A ceiling the session cannot pass, however long it runs.
    keep_max_frames: int = Field(default=2000, ge=1)
    #: Frames kept of any one tracked vehicle - far, middle, near. 0
    #: keeps every frame a detection lands on.
    frames_per_vehicle: int = Field(default=3, ge=0, le=50)
    #: Also run a small vehicle model, and keep frames where it sees a
    #: vehicle the plate model found no plate on.
    find_misses: bool = True


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
    #: How many captured frames were kept for labelling.
    frames_saved: int = 0
    frames_skipped_repeat: int = 0
    possible_misses_saved: int = 0
    stopped: bool
    error: str | None


class LiveCameraRead(BaseModel):
    """A camera this project watches, and how it was last watched."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    rtsp_url: str
    expected_fps: float
    model_id: str | None
    keep_frames: bool
    keep_every: int
    last_used_at: datetime
