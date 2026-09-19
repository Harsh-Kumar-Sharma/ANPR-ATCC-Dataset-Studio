"""The labelling queue and the boxes on a frame.

Two routers: the queue is listed under its project, but a frame is
addressed by its own id - the project is derivable from it, and the
canvas holds a frame, not a project.
"""

from dataclasses import asdict

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.api.projects import get_project_or_404
from app.db.models.annotation import Annotation
from app.db.models.frame import Frame
from app.db.session import get_db
from app.schemas.frame import (
    DeletedFrameRead,
    FrameAnnotationsReplace,
    FrameRead,
    FrameStatusWrite,
    QueueProgress,
)
from app.schemas.ocr import PlateReadingRead
from app.schemas.review import AnnotationRead
from app.services import frame_deletion, frames
from app.services.plate_text import plate_readings_for_frame

project_frames_router = APIRouter(prefix="/projects/{project_id}/frames", tags=["frames"])
frames_router = APIRouter(prefix="/frames", tags=["frames"])


@project_frames_router.get("", response_model=list[FrameRead])
def list_frames(project_id: str, status: str | None = None, db: Session = Depends(get_db)) -> list[Frame]:
    """The project's labelling queue, in labelling order."""
    get_project_or_404(db, project_id)
    return frames.list_queue(db, project_id, status=status)


@project_frames_router.get("/progress", response_model=QueueProgress)
def get_queue_progress(project_id: str, db: Session = Depends(get_db)) -> QueueProgress:
    """Labelled, rejected and remaining, for this project's whole queue."""
    get_project_or_404(db, project_id)
    return QueueProgress(**frames.queue_progress(db, project_id))


@frames_router.get("/{frame_id}", response_model=FrameRead)
def get_frame(frame_id: str, db: Session = Depends(get_db)) -> Frame:
    return frames.get_frame(db, frame_id)


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
