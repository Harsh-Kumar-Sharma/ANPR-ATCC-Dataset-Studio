import uuid
from datetime import datetime, timezone

from sqlalchemy import Float, ForeignKey, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TrainingRun(Base):
    """One attempt at training a model, and what it was made from.

    The point of recording this is traceability: a model in the models
    directory is a file, and six weeks later nobody remembers which
    labels went into it. A row here says which dataset version, which
    model it started from, and what settings were used - so a model
    can be traced back to the data that made it, and a chain of models
    each trained from the last can be followed back to the base.
    """

    __tablename__ = "training_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    #: The immutable export this was trained on.
    dataset_version_id: Mapped[str] = mapped_column(ForeignKey("dataset_versions.id"), nullable=False)
    #: The model it started from - a built-in, or one this app trained
    #: earlier, which is what makes the lineage a chain.
    base_model_id: Mapped[str] = mapped_column(String(128), nullable=False)
    #: The job carrying it, so progress and cancelling go through the
    #: machinery that already exists for long work.
    job_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

    epochs: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    image_size: Mapped[int] = mapped_column(Integer, nullable=False, default=640)
    #: Everything else that was passed, kept as given rather than
    #: spread over columns that would need a migration each.
    settings_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    #: The model id the finished weights were imported under. Null
    #: until it finishes; that is how "trained but nothing to show"
    #: stays impossible to confuse with "finished".
    output_model_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    #: The validation number the run ended on, for comparing attempts.
    best_map50: Mapped[float | None] = mapped_column(Float, nullable=True)
    #: How far it got. Kept for a cancelled run too - it says how much
    #: of the work was done before it stopped.
    last_epoch: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(2048), nullable=True)

    started_at: Mapped[datetime] = mapped_column(default=_utcnow, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)
