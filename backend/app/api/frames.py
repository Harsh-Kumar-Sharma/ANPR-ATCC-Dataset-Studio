"""The labelling queue and the boxes on a frame.

Two routers: the queue is listed under its project, but a frame is
addressed by its own id - the project is derivable from it, and the
canvas holds a frame, not a project.
"""

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.api.projects import get_project_or_404
from app.db.models.annotation import Annotation
from app.db.models.frame import Frame
from app.db.session import get_db
from app.schemas.frame import FrameAnnotationsReplace, FrameRead
from app.schemas.review import AnnotationRead
from app.services import frames

project_frames_router = APIRouter(prefix="/projects/{project_id}/frames", tags=["frames"])
frames_router = APIRouter(prefix="/frames", tags=["frames"])


@project_frames_router.get("", response_model=list[FrameRead])
def list_frames(project_id: str, status: str | None = None, db: Session = Depends(get_db)) -> list[Frame]:
    """The project's labelling queue, in labelling order."""
    get_project_or_404(db, project_id)
    return frames.list_queue(db, project_id, status=status)


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
        frames.BoxInput(class_id=a.class_id, bbox=a.bbox_json, attributes=a.attributes) for a in payload.annotations
    ]
    saved = frames.replace_annotations(db, project.id, frame, boxes)
    db.commit()
    for annotation in saved:
        db.refresh(annotation)
    return saved
