"""Seeing what the app is holding, and getting it back.

Deliberately not under a project: the question "where did my disk go"
is asked across all of them, and the answer includes workspaces that
belong to no project at all.

Every action here works while a job is running. That is exactly when
the disk fills and exactly when stopping everything to tidy up costs
the most.
"""

from dataclasses import asdict
from pathlib import Path

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.projects import get_project_or_404
from app.core.config import get_settings
from app.db.session import get_db
from app.schemas.storage import ClearFrameImagesRequest, ReclaimedRead, StorageUsageRead
from app.services import storage

router = APIRouter(prefix="/storage", tags=["storage"])


@router.get("", response_model=StorageUsageRead)
def get_storage_usage(db: Session = Depends(get_db)) -> StorageUsageRead:
    """What the app is holding, largest project first, plus the room
    left on the drive."""
    settings = get_settings()
    return StorageUsageRead(
        **asdict(storage.usage(db, Path(settings.workspace_root), Path(settings.jobs_dir)))
    )


@router.post("/clear-frame-images", response_model=ReclaimedRead)
def clear_frame_images(payload: ClearFrameImagesRequest, db: Session = Depends(get_db)) -> ReclaimedRead:
    """Delete decoded frame images for a project, or for one source.

    The cheapest thing to reclaim and the safest: a frame image is a
    cache of the source video, and the next time the canvas opens one
    it is decoded again. Labels, boxes and frame rows are untouched.
    """
    project = get_project_or_404(db, payload.project_id)
    reclaimed = storage.clear_frame_images(db, project, payload.source_id)
    db.commit()
    return ReclaimedRead(**asdict(reclaimed))


@router.post("/clear-job-files", response_model=ReclaimedRead)
def clear_job_files(db: Session = Depends(get_db)) -> ReclaimedRead:
    """Remove the progress file and log of every finished job.

    A running worker is still writing to its own, so those stay.
    """
    reclaimed = storage.clear_job_files(db, Path(get_settings().jobs_dir))
    return ReclaimedRead(**asdict(reclaimed))


@router.post("/remove-orphan-workspaces", response_model=ReclaimedRead)
def remove_orphan_workspaces(db: Session = Depends(get_db)) -> ReclaimedRead:
    """Delete workspace directories that belong to no project."""
    reclaimed = storage.remove_orphan_workspaces(db, Path(get_settings().workspace_root))
    return ReclaimedRead(**asdict(reclaimed))
