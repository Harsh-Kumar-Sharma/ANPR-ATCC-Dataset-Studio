"""The cameras a project watches, and how they were last watched.

Remembered so that starting a capture again is one click rather than
four fields. Written by the start endpoint itself rather than by a
separate "save" the user has to remember to press: the settings they
just started with are, by definition, the ones worth keeping.
"""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
from app.db.models.live_camera import LiveCamera


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def list_cameras(db: Session, project_id: str) -> list[LiveCamera]:
    """This project's cameras, most recently used first.

    That order is the whole point: the camera you stopped a minute ago
    is the one you are most likely to want back.
    """
    return list(
        db.scalars(
            select(LiveCamera)
            .where(LiveCamera.project_id == project_id)
            .order_by(LiveCamera.last_used_at.desc())
        )
    )


def remember(
    db: Session,
    project_id: str,
    rtsp_url: str,
    expected_fps: float,
    model_id: str | None,
    keep_frames: bool,
    keep_every: int,
) -> LiveCamera:
    """Record how this camera was just started.

    An existing row for the same URL is updated rather than a second
    one added. Two rows for one camera would be two entries in the
    picker with the same name and different settings, which is worse
    than not remembering at all.
    """
    camera = db.scalar(
        select(LiveCamera).where(LiveCamera.project_id == project_id, LiveCamera.rtsp_url == rtsp_url)
    )
    if camera is None:
        camera = LiveCamera(project_id=project_id, rtsp_url=rtsp_url)
        db.add(camera)

    camera.expected_fps = expected_fps
    camera.model_id = model_id
    camera.keep_frames = keep_frames
    camera.keep_every = keep_every
    camera.last_used_at = _utcnow()
    db.flush()
    return camera


def forget(db: Session, camera_id: str) -> None:
    """Stop offering this camera.

    Deletes only the remembered settings. Sources, runs and anything
    captured from the camera are untouched - forgetting a shortcut is
    not the same as deleting footage, and conflating the two would be
    a nasty surprise.
    """
    camera = db.get(LiveCamera, camera_id)
    if camera is None:
        raise NotFoundError(f"No remembered camera: {camera_id}")
    db.delete(camera)
