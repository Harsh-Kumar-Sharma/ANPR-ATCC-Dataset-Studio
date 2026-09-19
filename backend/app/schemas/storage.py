from pydantic import BaseModel


class ProjectUsageRead(BaseModel):
    """One project's footprint, split the way a person would decide.

    The four buckets are ordered by how safe they are to reclaim, not
    by size: a decoded frame is a cache, an export is a snapshot, a
    crop can be made again by reprocessing, and the source video is the
    footage itself.
    """

    project_id: str
    name: str
    source_videos_bytes: int
    track_crops_bytes: int
    frame_images_bytes: int
    exports_bytes: int
    total_bytes: int


class OrphanWorkspaceRead(BaseModel):
    """A workspace directory belonging to no project."""

    path: str
    bytes: int


class StorageUsageRead(BaseModel):
    free_bytes: int
    total_bytes: int
    app_bytes: int
    #: Progress files and worker logs, which live outside every project.
    job_files_bytes: int
    projects: list[ProjectUsageRead]
    orphan_workspaces: list[OrphanWorkspaceRead]


class ClearFrameImagesRequest(BaseModel):
    project_id: str
    #: Narrow it to one source. Omitted means the whole project.
    source_id: str | None = None


class ReclaimedRead(BaseModel):
    reclaimed_bytes: int
    #: A plain sentence saying what went, for the UI to show back.
    detail: str
