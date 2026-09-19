"""Removing a source, and everything derived from it.

The project delete in miniature, and it wants the same interlocks: say
what would go, refuse while work is running against it, and never
delete a file that is not inside this project's own workspace.

One thing is deliberately *not* removed: a dataset version already
exported. Those are immutable by design, they belong to the project
rather than to one source, and something may already have trained on
one. Deleting a source rewrites what the project will export next; it
does not rewrite what it exported last.

The files are removed by name rather than by clearing a directory. A
source owns its copy of the video, the crops of its own tracks and the
frame images decoded from it - each of which lives at a known path -
while the directories around them are shared with every other source
in the project.
"""

from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import ConflictError
from app.db.models.annotation import Annotation
from app.db.models.frame import Frame
from app.db.models.frame_candidate import FrameCandidate
from app.db.models.job import TERMINAL_JOB_STATUSES, Job
from app.db.models.ocr_candidate import OcrCandidate
from app.db.models.processing_run import ProcessingRun
from app.db.models.source import Source
from app.db.models.track import Track
from app.services.cascade import (
    delete_in,
    directory_size,
    file_size,
    ids_in,
    is_inside,
    remove_file,
    remove_tree,
)
from app.services.frame_materializer import frames_root
from app.services.thumbnails import thumbs_root
from app.services.project_deletion import UNFINISHED_RUN_STATUSES


class SourceBusyError(ConflictError):
    """Work is running against this source.

    A detect worker or a live capture thread is still writing to these
    rows and these files. Deleting underneath it leaves a process
    filling a workspace for a source that no longer exists.
    """

    code = "source_busy"


@dataclass
class SourceContents:
    """What a source holds, in the terms a person would miss it in."""

    frames: int = 0
    tracks: int = 0
    #: Human boxes. Predictions are not counted - nobody mourns one.
    labels: int = 0
    #: Its copy of the video, its track crops and its decoded frames.
    #: Dataset exports are not counted, because they are not removed.
    bytes: int = 0
    #: Unfinished jobs and live captures. Any at all and deletion refuses.
    running_jobs: int = 0
    #: Whether the files actually went.
    files_removed: bool = False


@dataclass
class _Owned:
    """The rows and paths hanging off one source."""

    run_ids: list[str]
    track_ids: list[str]
    frame_ids: list[str]
    annotation_ids: list[str]
    video: Path | None
    frames_dir: Path | None
    #: Small pictures made for the contact sheet. Derived, and
    #: worthless once the frames they show are gone.
    thumbs_dir: Path | None
    track_dirs: list[Path]


def summarize(db: Session, source: Source, workspace_path: Path) -> SourceContents:
    """Count what deleting this source would destroy."""
    owned = _owned(db, source, workspace_path)

    return SourceContents(
        frames=len(owned.frame_ids),
        tracks=len(owned.track_ids),
        labels=_count_labels(db, owned.frame_ids),
        bytes=(
            file_size(owned.video)
            + directory_size(owned.frames_dir)
            + directory_size(owned.thumbs_dir)
            + sum(directory_size(d) for d in owned.track_dirs)
        ),
        running_jobs=_count_unfinished_work(db, source),
    )


def delete_source(db: Session, source: Source, workspace_path: Path) -> tuple[SourceContents, _Owned]:
    """Delete a source's rows. The caller commits, then removes the files.

    Rows first and files after, for the same reason the project delete
    settled on: a filesystem cannot join the transaction, so one of the
    two failure modes has to be chosen. Files left behind can be deleted
    by hand; rows left pointing at images that are gone cannot be
    reasoned about at all.
    """
    contents = summarize(db, source, workspace_path)
    if contents.running_jobs:
        raise SourceBusyError(
            f"{contents.running_jobs} job(s) or live session(s) are still running against this source. "
            "Wait for them to finish, cancel them, or stop the capture, then delete it."
        )

    owned = _owned(db, source, workspace_path)

    # Children first, so each statement can be read against the parent
    # that is still there when it runs.
    delete_in(db, Annotation, Annotation.id, owned.annotation_ids)
    delete_in(db, OcrCandidate, OcrCandidate.track_id, owned.track_ids)
    delete_in(db, FrameCandidate, FrameCandidate.track_id, owned.track_ids)
    delete_in(db, Track, Track.id, owned.track_ids)
    delete_in(db, Frame, Frame.id, owned.frame_ids)
    delete_in(db, ProcessingRun, ProcessingRun.id, owned.run_ids)
    db.delete(source)
    db.flush()

    return contents, owned


def remove_source_files(owned: _Owned) -> bool:
    """Delete the files the source owned, after the commit.

    Every path was already checked to be inside this project's own
    workspace when it was gathered; anything that was not is simply
    absent from the lists.
    """
    removed = False
    if owned.video is not None:
        removed = remove_file(owned.video) or removed
    if owned.frames_dir is not None:
        removed = remove_tree(owned.frames_dir) or removed
    if owned.thumbs_dir is not None:
        removed = remove_tree(owned.thumbs_dir) or removed
    for directory in owned.track_dirs:
        removed = remove_tree(directory) or removed
    return removed


def _owned(db: Session, source: Source, workspace_path: Path) -> _Owned:
    run_ids = [r for r in db.scalars(select(ProcessingRun.id).where(ProcessingRun.source_id == source.id))]
    track_ids = ids_in(db, Track.id, Track.run_id, run_ids)
    frame_ids = [f for f in db.scalars(select(Frame.id).where(Frame.source_id == source.id))]
    annotation_ids = ids_in(db, Annotation.id, Annotation.frame_id, frame_ids)

    return _Owned(
        run_ids=run_ids,
        track_ids=track_ids,
        frame_ids=frame_ids,
        annotation_ids=annotation_ids,
        video=_owned_video(source, workspace_path),
        frames_dir=_inside(frames_root(workspace_path) / source.id, workspace_path),
        thumbs_dir=_inside(thumbs_root(workspace_path) / source.id, workspace_path),
        track_dirs=[
            path
            for track_id in track_ids
            if (path := _inside(workspace_path / "derived" / "tracks" / track_id, workspace_path)) is not None
        ],
    )


def _owned_video(source: Source, workspace_path: Path) -> Path | None:
    """The copy of the video this project made at import.

    A live stream has a URL rather than a path, and a source imported
    before copying existed may point at the user's own file somewhere
    else entirely. Neither is ours to delete, and ``_inside`` is what
    tells them apart.
    """
    if source.type != "video" or not source.path_or_uri:
        return None
    return _inside(Path(source.path_or_uri), workspace_path)


def _inside(path: Path, workspace_path: Path) -> Path | None:
    return path if is_inside(path, workspace_path) else None


def _count_labels(db: Session, frame_ids: list[str]) -> int:
    if not frame_ids:
        return 0
    total = 0
    for chunk in _chunks(frame_ids):
        total += (
            db.scalar(
                select(func.count(Annotation.id)).where(
                    Annotation.frame_id.in_(chunk), Annotation.source == "human"
                )
            )
            or 0
        )
    return total


def _count_unfinished_work(db: Session, source: Source) -> int:
    """Jobs aimed at this source, plus live captures on it.

    A detect job names its run in ``params``, and its run names the
    source - so the run is what ties a job to a source, and counting
    unfinished runs catches both the detect worker and the RTSP session
    that never creates a job at all. A select job names the source
    outright.
    """
    runs = (
        db.scalar(
            select(func.count(ProcessingRun.id)).where(
                ProcessingRun.source_id == source.id,
                ProcessingRun.status.in_(UNFINISHED_RUN_STATUSES),
            )
        )
        or 0
    )
    selects = (
        db.scalar(
            select(func.count(Job.id)).where(
                Job.project_id == source.project_id,
                Job.status.not_in(TERMINAL_JOB_STATUSES),
                Job.params_json["source_id"].as_string() == source.id,
            )
        )
        or 0
    )
    return runs + selects


def _chunks(items: list[str]):
    from app.services.annotations import chunked

    return chunked(items)
