import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class LiveCamera(Base):
    """A camera this project watches, and how it was last watched.

    Starting a live capture used to mean retyping the URL, the frame
    rate, the model and the keep-frames settings every single time -
    including after a Stop, which is the most likely moment to want
    the same camera again.

    In the database rather than in browser storage because a camera is
    the project's, not this machine's: the URL, the model it is
    watched with and how much of it is kept are the same facts on any
    machine that opens the project. It is also the one thing here a
    user would be annoyed to lose.

    The password is in the URL, which is how RTSP works and how the
    source rows already store it. Nothing new is exposed by keeping it
    here, but it is worth knowing that this row is as sensitive as the
    camera's credentials.
    """

    __tablename__ = "live_cameras"
    # One row per camera per project. Starting the same URL again
    # updates the settings rather than growing a list of near-copies.
    __table_args__ = (UniqueConstraint("project_id", "rtsp_url", name="uq_live_camera_project_url"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    rtsp_url: Mapped[str] = mapped_column(String(1024), nullable=False)

    #: What the camera roughly sends. Sizes the tracker's
    #: lost-track-buffer; it does not set the capture speed.
    expected_fps: Mapped[float] = mapped_column(Float, nullable=False, default=10.0)
    #: Null means whatever the default model is at the time, rather
    #: than pinning a model that may since have been removed.
    model_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    keep_frames: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    keep_every: Mapped[int] = mapped_column(Integer, nullable=False, default=10)

    created_at: Mapped[datetime] = mapped_column(default=_utcnow, nullable=False)
    #: Which camera to offer first when there is more than one.
    last_used_at: Mapped[datetime] = mapped_column(default=_utcnow, nullable=False)
