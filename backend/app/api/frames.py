"""The labelling queue and the boxes on a frame.

Two routers: the queue is listed under its project, but a frame is
addressed by its own id - the project is derivable from it, and the
canvas holds a frame, not a project.
"""

from dataclasses import asdict
from pathlib import Path

from fastapi import APIRouter, Depends, Response
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.api.projects import get_project_or_404
from app.core.errors import NotFoundError
from app.db.models.annotation import Annotation
from app.db.models.frame import Frame
from app.db.models.source import Source
from app.db.session import get_db
from app.schemas.frame import (
    DeletedFrameRead,
    FrameAnnotationsReplace,
    FrameRead,
    FrameStatusWrite,
    QueueProgress,
    SourceQueueRead,
    SuggestedBoxRead,
    SweepRead,
)
from app.schemas.ocr import PlateReadingRead
from app.schemas.review import AnnotationRead
from app.services import frame_deletion, frame_sweep, frames, thumbnails
from app.services.plate_text import plate_readings_for_frame

project_frames_router = APIRouter(prefix="/projects/{project_id}/frames", tags=["frames"])
frames_router = APIRouter(prefix="/frames", tags=["frames"])


@project_frames_router.get("", response_model=list[FrameRead])
def list_frames(
    project_id: str,
    status: str | None = None,
    source_id: str | None = None,
    db: Session = Depends(get_db),
) -> list[Frame]:
    """The project's labelling queue, in labelling order.

    ``source_id`` narrows it to one clip, which is the only way the
    queue stays usable once a project has more than one source.
    """
    get_project_or_404(db, project_id)
    _require_source_of_project(db, project_id, source_id)
    return frames.list_queue(db, project_id, status=status, source_id=source_id)


@project_frames_router.get("/sweep", response_model=SweepRead)
def preview_sweep(project_id: str, source_id: str | None = None, db: Session = Depends(get_db)) -> SweepRead:
    """What deleting the unlabelled frames would take.

    Asked before the button, because a confirmation that cannot say
    what is about to go is not one.
    """
    get_project_or_404(db, project_id)
    _require_source_of_project(db, project_id, source_id)
    return SweepRead(**asdict(frame_sweep.preview(db, project_id, source_id=source_id)))


@project_frames_router.post("/sweep", response_model=SweepRead)
def sweep_unlabelled(project_id: str, source_id: str | None = None, db: Session = Depends(get_db)) -> SweepRead:
    """Keep the frames that were labelled; delete the rest for good.

    The point of keeping a live session's frames is to have something
    to label. Once that is done, the ones nobody labelled are a road
    with nothing on it, one 1080p JPEG at a time.

    Frames a dataset version already names are held back rather than
    deleted - the version is the record of what a model was trained
    on, and it has to keep describing real files.
    """
    get_project_or_404(db, project_id)
    _require_source_of_project(db, project_id, source_id)
    done, images = frame_sweep.sweep(db, project_id, source_id=source_id)
    db.commit()
    # After the commit, deliberately: a file left behind can be
    # deleted later, a row pointing at a file that has gone cannot be
    # fixed at all.
    frame_sweep.remove_swept_images(images)
    return SweepRead(**asdict(done))


@project_frames_router.get("/by-source", response_model=list[SourceQueueRead])
def get_queue_by_source(project_id: str, db: Session = Depends(get_db)) -> list[SourceQueueRead]:
    """Each source with its own progress, most work left first.

    One call rather than one per source, so the picker can say which
    clip still needs doing without a request per row.
    """
    get_project_or_404(db, project_id)
    return [SourceQueueRead(**asdict(queue)) for queue in frames.queue_by_source(db, project_id)]


@project_frames_router.get("/progress", response_model=QueueProgress)
def get_queue_progress(
    project_id: str, source_id: str | None = None, db: Session = Depends(get_db)
) -> QueueProgress:
    """Labelled, rejected and remaining, for this project's queue.

    Narrowed by ``source_id`` to the same clip the list is showing. A
    progress line counting the whole project beside a list showing one
    source is worse than no progress line at all.
    """
    get_project_or_404(db, project_id)
    _require_source_of_project(db, project_id, source_id)
    return QueueProgress(**frames.queue_progress(db, project_id, source_id=source_id))


def _require_source_of_project(db: Session, project_id: str, source_id: str | None) -> None:
    """A source id that is not this project's is an error, not an empty list.

    An empty answer reads identically to "this source has nothing to
    label", which is the wrong thing to tell someone whose id is stale
    or mistyped.
    """
    if source_id is None:
        return
    source = db.get(Source, source_id)
    if source is None or source.project_id != project_id:
        raise NotFoundError(f"Source not found in this project: {source_id}")


@frames_router.get("/{frame_id}", response_model=FrameRead)
def get_frame(frame_id: str, db: Session = Depends(get_db)) -> Frame:
    return frames.get_frame(db, frame_id)


@frames_router.get("/{frame_id}/suggestions", response_model=list[SuggestedBoxRead])
def get_frame_suggestions(frame_id: str, db: Session = Depends(get_db)) -> list[SuggestedBoxRead]:
    """What the model found on this frame, strongest first.

    The canvas opens with these already drawn, so labelling a frame
    the model got right is a save rather than a redraw.
    """
    frame = frames.get_frame(db, frame_id)
    return [
        SuggestedBoxRead(
            frame_candidate_id=candidate.id,
            bbox_json=list(candidate.bbox_json),
            detector_class=candidate.detector_class,
            detector_confidence=candidate.detector_confidence,
        )
        for candidate in frames.suggested_boxes(db, frame)
    ]


@frames_router.get("/{frame_id}/thumbnail")
def get_frame_thumbnail(frame_id: str, db: Session = Depends(get_db)) -> Response:
    """A small picture of this frame, for looking at many at once.

    Its own endpoint rather than a parameter on the full-size one
    because the costs are different in kind: a thumbnail is a few tens
    of kilobytes and never writes a full-size image, so a grid of two
    hundred frames does not decode two hundred 1080p files to disk.
    """
    frame = frames.get_frame(db, frame_id)
    project = frames.project_of(db, frame)
    return Response(
        content=thumbnails.thumbnail_jpeg(db, frame, Path(project.workspace_path)),
        media_type="image/jpeg",
        # Cached hard: a frame's pixels never change, and a grid
        # re-requests the same two hundred on every scroll.
        headers={"Cache-Control": "private, max-age=86400"},
    )


@frames_router.get("/{frame_id}/image")
def get_frame_image(frame_id: str, db: Session = Depends(get_db)) -> FileResponse:
    """The full frame, decoded from the source video the first time it is
    asked for. The canvas's <img> src."""
    frame = frames.get_frame(db, frame_id)
    path = frames.frame_image_path(db, frame)
    # The decode may have recorded image_path on the row.
    db.commit()
    return FileResponse(path, media_type="image/jpeg")


@frames_router.delete("/{frame_id}", response_model=DeletedFrameRead)
def delete_frame(frame_id: str, db: Session = Depends(get_db)) -> DeletedFrameRead:
    """Remove a frame for good - its boxes, its detections and its image.

    Not the same as skipping. Skipping is a judgement that can be
    reversed; this is for frames that should never have been offered,
    and the decoded image it removes is the part that fills a disk.

    Refused for a frame that is in an exported dataset version, because
    a version is immutable and its manifest names every image in it.
    """
    frame = frames.get_frame(db, frame_id)
    removed, image_path = frame_deletion.delete_frame(db, frame)
    db.commit()
    # After the commit: a file left behind can be deleted by hand, a row
    # pointing at an image that is gone cannot be reasoned about.
    removed.image_removed = frame_deletion.remove_frame_image(image_path)
    return DeletedFrameRead(**asdict(removed))


@frames_router.put("/{frame_id}/status", response_model=FrameRead)
def set_frame_status(frame_id: str, payload: FrameStatusWrite, db: Session = Depends(get_db)) -> Frame:
    """Skip a frame, or put a skipped one back.

    The boxes already on it are untouched either way - rejecting is a
    judgement about whether the frame is worth labelling, and it can be
    reversed.
    """
    frame = frames.get_frame(db, frame_id)
    frames.set_status(db, frame, payload.status)
    db.commit()
    db.refresh(frame)
    return frame


@frames_router.get("/{frame_id}/plate-readings", response_model=list[PlateReadingRead])
def list_frame_plate_readings(frame_id: str, db: Session = Depends(get_db)):
    """What the model read on this frame, best first.

    By frame because the canvas has no track - it holds a picture. Lets
    a labeller see what the model made of each plate instead of
    retyping one it already has.
    """
    frames.get_frame(db, frame_id)
    return [PlateReadingRead(**asdict(r)) for r in plate_readings_for_frame(db, frame_id)]


@frames_router.get("/{frame_id}/annotations", response_model=list[AnnotationRead])
def list_frame_annotations(frame_id: str, db: Session = Depends(get_db)) -> list[Annotation]:
    frames.get_frame(db, frame_id)
    return frames.list_annotations(db, frame_id)


@frames_router.put("/{frame_id}/annotations", response_model=list[AnnotationRead])
def replace_frame_annotations(
    frame_id: str, payload: FrameAnnotationsReplace, db: Session = Depends(get_db)
) -> list[Annotation]:
    """Save the complete set of boxes on a frame.

    Whole-set replacement: a box not in the payload is gone afterwards,
    which is how deleting works. An empty list is the label "nothing
    here". All-or-nothing - one invalid box leaves the previous set
    untouched.
    """
    frame = frames.get_frame(db, frame_id)
    project = frames.project_of(db, frame)
    boxes = [
        frames.BoxInput(id=a.id, class_id=a.class_id, bbox=a.bbox_json, attributes=a.attributes)
        for a in payload.annotations
    ]
    saved = frames.replace_annotations(db, project.id, frame, boxes)
    db.commit()
    for annotation in saved:
        db.refresh(annotation)
    return saved
