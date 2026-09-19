"""Deleting a project, and everything it owns.

The most destructive thing this app can do. A project owns its sources,
every frame sampled from them, every track and detection found in them,
every label a person drew, the dataset versions exported from those
labels, its own class list, and a workspace directory that on real
footage runs to gigabytes. None of it comes back, and nothing here is
recoverable from an export manifest the way a single annotation is.

So this module is mostly about refusing. It refuses while a job is
running, it refuses unless the caller names the project, and it refuses
to recursively delete a directory that is not where a workspace should
be. What is left after all that is an ordinary cascade.

Written by hand because nothing enforces the foreign keys - SQLite is
not asked to - so the database will happily keep rows pointing at a
project that is gone. Orphans do not announce themselves; they sit
there being counted by the next query that forgets to scope itself.
"""

from dataclasses import dataclass
from pathlib import Path
from shutil import rmtree

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.errors import ConflictError
from app.db.models.annotation import Annotation
from app.db.models.class_definition import ClassDefinition
from app.db.models.dataset_item import DatasetItem
from app.db.models.dataset_version import DatasetVersion
from app.db.models.frame import Frame
from app.db.models.frame_candidate import FrameCandidate
from app.db.models.job import TERMINAL_JOB_STATUSES, Job
from app.db.models.ocr_candidate import OcrCandidate
from app.db.models.processing_run import ProcessingRun
from app.db.models.project import Project
from app.db.models.source import Source
from app.db.models.track import Track
from app.services.workspace import project_workspace_path


class ProjectBusyError(ConflictError):
    """Work is running against this project.

    A detached worker is still writing to these rows and into that
    directory. Deleting underneath it leaves a process filling a
    workspace that belongs to nothing, and a half-written state nobody
    can reason about afterwards.
    """

    code = "project_busy"


class ProjectNameMismatchError(ConflictError):
    """The caller did not name the project they are deleting.

    The interlock. A mis-aimed request - the wrong id in a URL, a page
    left open while something else changed - should not be able to
    destroy a project the user was not looking at.
    """

    code = "name_mismatch"


@dataclass
class ProjectContents:
    """What a project holds, in the terms a person would miss it in.

    Shown before deleting and returned after, so what was promised and
    what happened can be compared.
    """

    sources: int = 0
    frames: int = 0
    tracks: int = 0
    #: Human boxes. Model predictions are not counted - nobody mourns a
    #: prediction, and counting them would inflate the number a
    #: confirmation dialog leans on.
    labels: int = 0
    dataset_versions: int = 0
    #: Size of the workspace directory. Usually the bulk of what is
    #: being destroyed, and the only part measured in gigabytes.
    workspace_bytes: int = 0
    #: Jobs that have not finished. Any at all and deletion refuses.
    running_jobs: int = 0
    #: Whether the workspace directory was actually removed. False when
    #: there was nothing there, or when it was somewhere this refuses to
    #: delete from.
    workspace_removed: bool = False


def summarize(db: Session, project: Project, workspace_root: Path) -> ProjectContents:
    """Count what deleting this project would destroy."""
    source_ids = _source_ids(db, project.id)

    return ProjectContents(
        sources=len(source_ids),
        frames=_count(db, Frame.id, Frame.source_id.in_(source_ids)) if source_ids else 0,
        tracks=_count_tracks(db, source_ids),
        labels=_count_labels(db, source_ids),
        dataset_versions=_count(db, DatasetVersion.id, DatasetVersion.project_id == project.id),
        workspace_bytes=_directory_size(_deletable_workspace(project, workspace_root)),
        running_jobs=_count(
            db, Job.id, Job.project_id == project.id, Job.status.not_in(TERMINAL_JOB_STATUSES)
        ),
    )


def delete_project(db: Session, project: Project, workspace_root: Path, *, confirm_name: str) -> ProjectContents:
    """Delete a project and everything it owns.

    ``confirm_name`` must equal the project's name exactly. Nothing is
    touched unless it does.

    The caller commits. Everything here is one transaction, so a failure
    part-way leaves the project whole rather than half-deleted - except
    the directory, which is removed last precisely because a filesystem
    cannot join the transaction. Rows without their files is a project
    that is gone; files without their rows is a directory the user can
    delete by hand.
    """
    if confirm_name != project.name:
        raise ProjectNameMismatchError(
            f"To delete this project, confirm its name exactly: {project.name!r}."
        )

    contents = summarize(db, project, workspace_root)
    if contents.running_jobs:
        raise ProjectBusyError(
            f"{contents.running_jobs} job(s) are still running for this project. "
            "Wait for them to finish or cancel them, then delete it."
        )

    source_ids = _source_ids(db, project.id)
    run_ids = (
        [r for r in db.scalars(select(ProcessingRun.id).where(ProcessingRun.source_id.in_(source_ids)))]
        if source_ids
        else []
    )
    track_ids = [t for t in db.scalars(select(Track.id).where(Track.run_id.in_(run_ids)))] if run_ids else []
    frame_ids = [f for f in db.scalars(select(Frame.id).where(Frame.source_id.in_(source_ids)))] if source_ids else []
    annotation_ids = (
        [a for a in db.scalars(select(Annotation.id).where(Annotation.frame_id.in_(frame_ids)))] if frame_ids else []
    )
    version_ids = [v for v in db.scalars(select(DatasetVersion.id).where(DatasetVersion.project_id == project.id))]

    # Children first, so a failure part-way never leaves a parent
    # pointing at rows that are gone.
    _delete_in(db, DatasetItem, DatasetItem.annotation_id, annotation_ids)
    _delete_in(db, DatasetItem, DatasetItem.dataset_version_id, version_ids)
    _delete_in(db, DatasetVersion, DatasetVersion.id, version_ids)
    _delete_in(db, Annotation, Annotation.id, annotation_ids)
    _delete_in(db, OcrCandidate, OcrCandidate.track_id, track_ids)
    _delete_in(db, FrameCandidate, FrameCandidate.track_id, track_ids)
    _delete_in(db, Track, Track.id, track_ids)
    _delete_in(db, Frame, Frame.id, frame_ids)
    _delete_in(db, ProcessingRun, ProcessingRun.id, run_ids)
    _delete_in(db, Source, Source.id, source_ids)
    db.execute(delete(ClassDefinition).where(ClassDefinition.project_id == project.id))
    db.execute(delete(Job).where(Job.project_id == project.id))
    db.delete(project)
    db.flush()

    workspace = _deletable_workspace(project, workspace_root)
    if workspace is not None and workspace.is_dir():
        rmtree(workspace, ignore_errors=True)
        contents.workspace_removed = not workspace.exists()

    return contents


def _deletable_workspace(project: Project, workspace_root: Path) -> Path | None:
    """The project's directory, if it is somewhere we may delete from.

    Two things have to hold: the path sits inside the configured
    workspace root, and its own name is the project id. Both are cheap,
    and together they mean a recursive delete can only ever reach a
    directory this application created for this project.

    The recorded ``workspace_path`` is a column. A recursive delete
    driven by a column is a recursive delete driven by whatever wrote
    it - a hand-edited row, a restored backup from a machine with a
    different layout - and the blast radius of getting that wrong is
    everything under whatever the column happens to say.
    """
    if not project.workspace_path:
        return None

    recorded = Path(project.workspace_path)
    expected = project_workspace_path(workspace_root, project.id)
    try:
        if recorded.resolve() != expected.resolve():
            return None
    except OSError:
        # An unresolvable path is not one to start deleting from.
        return None
    return recorded


def _directory_size(path: Path | None) -> int:
    if path is None or not path.is_dir():
        return 0
    total = 0
    for entry in path.rglob("*"):
        try:
            if entry.is_file():
                total += entry.stat().st_size
        except OSError:
            # A file that vanished while being counted is a file that is
            # not going to be there to delete either.
            continue
    return total


def _source_ids(db: Session, project_id: str) -> list[str]:
    return list(db.scalars(select(Source.id).where(Source.project_id == project_id)))


def _count(db: Session, column, *conditions) -> int:
    return db.scalar(select(func.count(column)).where(*conditions)) or 0


def _count_tracks(db: Session, source_ids: list[str]) -> int:
    if not source_ids:
        return 0
    return (
        db.scalar(
            select(func.count(Track.id))
            .join(ProcessingRun, Track.run_id == ProcessingRun.id)
            .where(ProcessingRun.source_id.in_(source_ids))
        )
        or 0
    )


def _count_labels(db: Session, source_ids: list[str]) -> int:
    if not source_ids:
        return 0
    return (
        db.scalar(
            select(func.count(Annotation.id))
            .join(Frame, Annotation.frame_id == Frame.id)
            .where(Frame.source_id.in_(source_ids), Annotation.source == "human")
        )
        or 0
    )


def _delete_in(db: Session, model, column, ids: list[str]) -> None:
    """Delete rows whose ``column`` is one of ``ids``, in chunks.

    SQLite caps a statement at 32 766 bound parameters and a real
    project has thousands of frames, so an ``IN`` over a materialised
    list has to be fed in pieces. Same helper and same bound as
    ``services/annotations.py``.
    """
    from app.services.annotations import chunked

    for chunk in chunked(ids):
        db.execute(delete(model).where(column.in_(chunk)))
