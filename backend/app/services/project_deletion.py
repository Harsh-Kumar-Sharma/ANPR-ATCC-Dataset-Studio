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
import json
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
from app.db.models.labeling_task import LabelingTask
from app.db.models.job import TERMINAL_JOB_STATUSES, Job
from app.db.models.ocr_candidate import OcrCandidate
from app.db.models.processing_run import ProcessingRun
from app.db.models.project import Project
from app.db.models.source import Source
from app.db.models.track import Track


#: A processing run that has not finished. Detection sets ``completed``
#: or ``failed`` when it is done; a live RTSP session leaves its run
#: ``running`` for as long as the camera is connected.
UNFINISHED_RUN_STATUSES = ("pending", "running")


class ProjectBusyError(ConflictError):
    """Work is running against this project.

    A detached worker or a live capture thread is still writing to these
    rows and into that directory. Deleting underneath it leaves a
    process filling a workspace that belongs to nothing, and a
    half-written state nobody can reason about afterwards.
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
    #: Jobs and processing runs that have not finished. Any at all and
    #: deletion refuses. Runs are counted as well as jobs because a live
    #: RTSP session has no job row at all - it starts capture threads and
    #: a ``running`` run, and is otherwise invisible to a job-only check.
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
        workspace_bytes=_directory_size(
            _deletable_workspace(project.id, project.workspace_path, workspace_root)
        ),
        running_jobs=_count_unfinished_work(db, project.id, source_ids),
    )


def delete_project(db: Session, project: Project, workspace_root: Path, *, confirm_name: str) -> ProjectContents:
    """Delete a project and everything it owns.

    ``confirm_name`` must equal the project's name exactly. Nothing is
    touched unless it does.

    Rows only. The caller commits, and only then removes the directory
    with ``remove_workspace`` - a filesystem cannot join the
    transaction, so one of the two failure modes has to be chosen, and
    files-without-rows is the survivable one. A crash during a
    multi-gigabyte ``rmtree`` then leaves a directory the user can
    delete by hand. Doing it the other way round leaves the project in
    the picker with every row intact and every image gone, which is the
    most confusing state this app can produce.
    """
    if confirm_name != project.name:
        # Deliberately does not repeat the name. A client that retries on
        # 409 could read it out of the refusal and resend, which would
        # make the interlock a guard against typing mistakes only.
        raise ProjectNameMismatchError("The name given does not match this project's name exactly.")

    contents = summarize(db, project, workspace_root)
    if contents.running_jobs:
        raise ProjectBusyError(
            f"{contents.running_jobs} job(s) or live session(s) are still running for this project. "
            "Wait for them to finish, cancel them, or stop the capture, then delete it."
        )

    # Gathered in chunks as well as deleted in chunks. A real project
    # holds thousands of frames, and one bound parameter per frame runs
    # into SQLite's 32 766 cap while *building* the annotation list -
    # which made a big enough project undeletable, with a 500 and
    # "An unexpected error occurred".
    source_ids = _source_ids(db, project.id)
    run_ids = _ids_in(db, ProcessingRun.id, ProcessingRun.source_id, source_ids)
    track_ids = _ids_in(db, Track.id, Track.run_id, run_ids)
    frame_ids = _ids_in(db, Frame.id, Frame.source_id, source_ids)
    annotation_ids = _ids_in(db, Annotation.id, Annotation.frame_id, frame_ids)
    version_ids = [v for v in db.scalars(select(DatasetVersion.id).where(DatasetVersion.project_id == project.id))]

    # Children first. Everything here is one transaction, so ordering
    # buys nothing on failure - it rolls back either way. It is for the
    # reader: each statement can be checked against the parent that is
    # still there when it runs.
    _delete_in(db, DatasetItem, DatasetItem.annotation_id, annotation_ids)
    _delete_in(db, DatasetItem, DatasetItem.dataset_version_id, version_ids)
    _delete_in(db, DatasetVersion, DatasetVersion.id, version_ids)
    _delete_in(db, Annotation, Annotation.id, annotation_ids)
    _delete_in(db, OcrCandidate, OcrCandidate.track_id, track_ids)
    _delete_in(db, FrameCandidate, FrameCandidate.track_id, track_ids)
    _delete_in(db, Track, Track.id, track_ids)
    _delete_in(db, Frame, Frame.id, frame_ids)
    _delete_in(db, ProcessingRun, ProcessingRun.id, run_ids)
    db.execute(delete(LabelingTask).where(LabelingTask.project_id == project.id))
    _delete_in(db, Source, Source.id, source_ids)
    db.execute(delete(ClassDefinition).where(ClassDefinition.project_id == project.id))
    db.execute(delete(Job).where(Job.project_id == project.id))
    db.delete(project)
    db.flush()
    return contents


def remove_workspace(project_id: str, workspace_path: str, workspace_root: Path) -> bool:
    """Delete the project's directory, if it is one of ours.

    Called after the commit, never before. Takes the id and path rather
    than the row because by then the row is gone.

    Returns whether the directory is actually gone, which the caller
    reports - a permission error or a file held open by the desktop app
    leaves files behind, and the user needs to hear that rather than be
    told it all went.
    """
    workspace = _deletable_workspace(project_id, workspace_path, workspace_root)
    if workspace is None or not workspace.is_dir():
        return False
    rmtree(workspace, ignore_errors=True)
    return not workspace.exists()


def _deletable_workspace(project_id: str, workspace_path: str, workspace_root: Path) -> Path | None:
    """The project's directory, if it is one this app made for it.

    The recorded ``workspace_path`` is a column. A recursive delete
    driven by a column is a recursive delete driven by whatever wrote
    it - a hand-edited row, a backup restored from a machine with a
    different layout - and the blast radius of getting that wrong is
    everything under whatever the column happens to say.

    Three things have to hold, and the third is the one that does the
    work:

    1. the path resolves inside the resolved workspace root;
    2. its own name is the project id;
    3. it contains the ``project.json`` this app writes at creation,
       naming this same project.

    The first two alone were not a check at all. Both the recorded path
    and the expected one are built from the same relative default
    (``data/workspace``), so they resolve against the process's working
    directory and always agree - the comparison could not fail, and what
    ``rmtree`` was aimed at was decided by where the backend happened to
    be started from rather than by the column. The manifest is what
    makes the answer about the directory itself.
    """
    if not workspace_path:
        return None

    try:
        recorded = Path(workspace_path).resolve()
        root = Path(workspace_root).resolve()
    except OSError:
        # An unresolvable path is not one to start deleting from.
        return None

    if recorded == root or root not in recorded.parents:
        return None
    if recorded.name != project_id:
        return None
    if not _is_our_workspace(recorded, project_id):
        return None
    return recorded


def _is_our_workspace(path: Path, project_id: str) -> bool:
    """Does this directory carry the manifest we wrote into it?

    ``create_project_workspace`` writes ``project.json`` naming the
    project. Reading it back is the difference between "this path looks
    right" and "this is the directory we made", and it costs one small
    file read before a recursive delete.
    """
    manifest = path / "project.json"
    if not manifest.is_file():
        return False
    try:
        return json.loads(manifest.read_text(encoding="utf-8")).get("project_id") == project_id
    except (OSError, ValueError):
        return False


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


def _count_unfinished_work(db: Session, project_id: str, source_ids: list[str]) -> int:
    """Jobs and processing runs that have not finished.

    Both, because they are not the same set. Detection submits a job
    *and* creates a run; a live RTSP session creates only a run, so a
    job-only check saw nothing and let a project be deleted out from
    under a camera that was still capturing into its workspace.
    """
    jobs = _count(db, Job.id, Job.project_id == project_id, Job.status.not_in(TERMINAL_JOB_STATUSES))
    if not source_ids:
        return jobs

    # Only the live captures, not every unfinished run. A detect job
    # creates a run of its own, so counting both would report two things
    # in flight for one piece of work. An RTSP session is the case a job
    # count misses entirely: it starts capture threads and a ``running``
    # run, and never creates a job row.
    #
    # Filtered in Python because the marker lives in a JSON column and
    # there are only ever a handful of unfinished runs to look at.
    live = 0
    for chunk in _chunked(source_ids):
        for run in db.scalars(
            select(ProcessingRun).where(
                ProcessingRun.source_id.in_(chunk),
                ProcessingRun.status.in_(UNFINISHED_RUN_STATUSES),
            )
        ):
            if (run.sampling_config or {}).get("protocol") == "rtsp":
                live += 1
    return jobs + live


def _ids_in(db: Session, id_column, match_column, values: list[str]) -> list[str]:
    """Ids whose ``match_column`` is one of ``values``, gathered in chunks."""
    if not values:
        return []
    found: list[str] = []
    for chunk in _chunked(values):
        found.extend(db.scalars(select(id_column).where(match_column.in_(chunk))))
    return found


def _chunked(items: list):
    from app.services.annotations import chunked

    return chunked(items)


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
    for chunk in _chunked(ids):
        db.execute(delete(model).where(column.in_(chunk)))
