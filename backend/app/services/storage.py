"""What the app is holding on disk, and how to get it back.

Space is the user's to manage. The app's job is to make it visible and
freeable at any moment - including while a long run is going, which is
exactly when the disk fills and exactly when stopping everything to
tidy up costs the most.

**One rule runs through all of it: nothing here removes a label, an
annotation or a frame row.** Only two kinds of thing are reclaimable.
Pixels that can be regenerated - a decoded frame is a cache of the
source video, recoverable by decoding it again - and a dataset export
the user names outright, which is a snapshot rather than the work it
was made from.
"""

import shutil
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.db.models.frame import Frame
from app.db.models.job import TERMINAL_JOB_STATUSES, Job
from app.db.models.project import Project
from app.db.models.source import Source
from app.services.cascade import directory_size, remove_tree
from app.services.frame_materializer import frames_root


@dataclass
class ProjectUsage:
    """One project's footprint, split the way a person would decide."""

    project_id: str
    name: str
    #: The copies made at import. Deleting one loses the footage.
    source_videos_bytes: int = 0
    #: One per detection. Regenerable by processing the source again.
    track_crops_bytes: int = 0
    #: Decoded on demand. Pure cache - the cheapest thing to reclaim.
    frame_images_bytes: int = 0
    #: Exported datasets. A snapshot, deletable by name.
    exports_bytes: int = 0
    total_bytes: int = 0


@dataclass
class OrphanWorkspace:
    """A workspace directory belonging to no project.

    Left by a project deleted before there was a delete that cleaned
    up. Two were already on the machine this was written for.
    """

    path: str
    bytes: int


@dataclass
class StorageUsage:
    free_bytes: int = 0
    total_bytes: int = 0
    #: Everything the app is holding, across every project.
    app_bytes: int = 0
    #: Progress files and worker logs. They live outside every
    #: workspace, which is why nothing used to clean them up.
    job_files_bytes: int = 0
    projects: list[ProjectUsage] = field(default_factory=list)
    orphan_workspaces: list[OrphanWorkspace] = field(default_factory=list)


@dataclass
class Reclaimed:
    reclaimed_bytes: int = 0
    #: A plain sentence for the UI - what went, in what terms.
    detail: str = ""


def usage(db: Session, workspace_root: Path, jobs_dir: Path) -> StorageUsage:
    """What is on disk, largest project first."""
    drive = shutil.disk_usage(_existing_ancestor(workspace_root))

    projects = [_project_usage(db, project) for project in db.scalars(select(Project))]
    projects.sort(key=lambda p: p.total_bytes, reverse=True)

    known = {Path(p.workspace_path).name for p in db.scalars(select(Project)) if p.workspace_path}
    orphans = []
    if workspace_root.is_dir():
        for entry in sorted(workspace_root.iterdir()):
            if entry.is_dir() and entry.name not in known:
                orphans.append(OrphanWorkspace(path=str(entry), bytes=directory_size(entry)))

    return StorageUsage(
        free_bytes=drive.free,
        total_bytes=drive.total,
        app_bytes=sum(p.total_bytes for p in projects) + sum(o.bytes for o in orphans),
        job_files_bytes=directory_size(jobs_dir),
        projects=projects,
        orphan_workspaces=orphans,
    )


def clear_frame_images(db: Session, project: Project, source_id: str | None = None) -> Reclaimed:
    """Delete decoded frame images and forget where they were.

    Safe because a frame image is a cache. ``Frame.image_path`` is set
    back to null, which is how the materialiser already spells "not
    decoded yet, decode it on demand" - so the next time the canvas
    opens one it comes straight back out of the source video.

    The frame rows and every box on them are untouched.
    """
    workspace = Path(project.workspace_path)
    source_ids = [
        s for s in db.scalars(select(Source.id).where(Source.project_id == project.id))
    ]
    if source_id is not None:
        source_ids = [s for s in source_ids if s == source_id]

    reclaimed = 0
    for one in source_ids:
        directory = frames_root(workspace) / one
        reclaimed += directory_size(directory)
        remove_tree(directory)
        db.execute(update(Frame).where(Frame.source_id == one).values(image_path=None))
    db.flush()

    return Reclaimed(
        reclaimed_bytes=reclaimed,
        detail=f"Cleared decoded frames for {len(source_ids)} source(s). They decode again when next opened.",
    )


def clear_job_files(db: Session, jobs_dir: Path) -> Reclaimed:
    """Remove the progress file and log of every finished job.

    A running worker is still writing to its own, so only finished jobs
    are touched. Files belonging to no job row at all go too - those
    are the leftovers of jobs dismissed before this existed.
    """
    from app.services.jobs import runner

    if not jobs_dir.is_dir():
        return Reclaimed(detail="No job files to clear.")

    unfinished = {
        job_id
        for job_id in db.scalars(select(Job.id).where(Job.status.not_in(TERMINAL_JOB_STATUSES)))
    }

    reclaimed = 0
    removed = 0
    for job_id in db.scalars(select(Job.id).where(Job.status.in_(TERMINAL_JOB_STATUSES))):
        for path in (runner.progress_path(job_id), runner.log_path(job_id)):
            if path.is_file():
                reclaimed += path.stat().st_size
                path.unlink(missing_ok=True)
                removed += 1

    for entry in jobs_dir.iterdir():
        if not entry.is_file():
            continue
        owner = entry.name.split(".")[0]
        if owner in unfinished:
            continue
        if any(owner == job_id for job_id in unfinished):
            continue
        # Belongs to no job row at all - a job dismissed before these
        # files were cleaned up with it.
        if db.get(Job, owner) is None:
            reclaimed += entry.stat().st_size
            entry.unlink(missing_ok=True)
            removed += 1

    return Reclaimed(reclaimed_bytes=reclaimed, detail=f"Removed {removed} job file(s).")


def remove_orphan_workspaces(db: Session, workspace_root: Path) -> Reclaimed:
    """Delete workspace directories that belong to no project.

    The name has to be a directory directly under the workspace root
    and match no project's recorded workspace. Anything a project still
    claims is left alone, whatever else is in there.
    """
    if not workspace_root.is_dir():
        return Reclaimed(detail="No workspace directory yet.")

    known = {Path(p.workspace_path).name for p in db.scalars(select(Project)) if p.workspace_path}

    reclaimed = 0
    removed = 0
    for entry in sorted(workspace_root.iterdir()):
        if not entry.is_dir() or entry.name in known:
            continue
        reclaimed += directory_size(entry)
        if remove_tree(entry):
            removed += 1

    return Reclaimed(reclaimed_bytes=reclaimed, detail=f"Removed {removed} abandoned workspace(s).")


def _project_usage(db: Session, project: Project) -> ProjectUsage:
    workspace = Path(project.workspace_path) if project.workspace_path else None
    if workspace is None or not workspace.is_dir():
        return ProjectUsage(project_id=project.id, name=project.name)

    videos = directory_size(workspace / "source")
    crops = directory_size(workspace / "derived" / "tracks")
    frames = directory_size(frames_root(workspace))
    exports = directory_size(workspace / "exports")

    return ProjectUsage(
        project_id=project.id,
        name=project.name,
        source_videos_bytes=videos,
        track_crops_bytes=crops,
        frame_images_bytes=frames,
        exports_bytes=exports,
        # The whole directory, so anything not in the four buckets above
        # still shows up in the total rather than quietly going missing.
        total_bytes=directory_size(workspace),
    )


def _existing_ancestor(path: Path) -> Path:
    """The nearest directory that exists, for asking about the drive.

    The workspace root may not have been created yet on a fresh
    install, and ``disk_usage`` needs something real.
    """
    candidate = path.resolve()
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    return candidate
