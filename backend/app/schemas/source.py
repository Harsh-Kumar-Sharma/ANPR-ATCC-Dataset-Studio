from datetime import datetime

from pydantic import BaseModel, ConfigDict


class SourceImportRequest(BaseModel):
    """Import a video already sitting on the local filesystem.

    The desktop app resolves an absolute path via its native file
    picker before calling this endpoint (local-first: no upload step).
    """

    path: str


class SourceRename(BaseModel):
    #: Empty clears it, back to the file name or camera.
    name: str


class DatasetImportRequest(BaseModel):
    """Import a YOLO dataset already on the server - a ``.zip`` or the
    folder it unpacks to. In a deployment, the incoming folder."""

    path: str


class SourceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    name: str | None = None
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
    #: Frames this source actually holds, which is not ``frame_count``.
    #: For a live stream ``frame_count`` is a 0 sentinel - there is no
    #: known length - so the list was reading "0 frames" for a source
    #: holding three hundred.
    stored_frames: int = 0


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


class DatasetImportRead(BaseModel):
    """What an imported dataset became, and what it left out."""

    source: SourceRead
    images_imported: int
    boxes_imported: int
    #: Already in the project from an earlier import, so not added again.
    already_imported: int
    #: Named like images but would not open.
    unreadable: int
    #: Frames put first because a box sits where boxes sit in many pictures.
    repeated_spot_frames: int
    #: Frames put next because the model was unsure of a box.
    low_confidence_frames: int
    #: Dataset class name -> the project class its boxes went into.
    class_mapping: dict[str, str]
