from datetime import datetime

from pydantic import BaseModel, ConfigDict


class SourceImportRequest(BaseModel):
    """Import a video already sitting on the local filesystem.

    The desktop app resolves an absolute path via its native file
    picker before calling this endpoint (local-first: no upload step).
    """

    path: str


class SourceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    type: str
    path_or_uri: str
    fps: float
    width: int
    height: int
    duration_ms: int
    frame_count: int
    created_at: datetime
    is_frozen: bool
    ground_truth_vehicle_count: int | None
    is_processing: bool = False


class SourceContentsRead(BaseModel):
    """What a source holds, in the terms a person would miss it in.

    Shown before deleting and returned after, so the confirmation and
    the outcome can be compared. Dataset exports are absent on purpose:
    a version already exported is immutable and is not removed.
    """

    frames: int
    tracks: int
    #: Human boxes. Predictions are not counted.
    labels: int
    #: Its copy of the video, its track crops and its decoded frames.
    bytes: int
    #: Any at all and deletion refuses.
    running_jobs: int
    files_removed: bool = False
