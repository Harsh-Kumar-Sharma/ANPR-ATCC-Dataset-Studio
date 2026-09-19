from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class TrainingStartRequest(BaseModel):
    """Start a training run on an exported dataset version."""

    dataset_version_id: str
    #: The model to start from. A built-in, or one this app trained
    #: earlier - which is what makes a chain of models possible.
    base_model_id: str
    epochs: int = Field(default=100, ge=1, le=1000)
    image_size: int = Field(default=640, ge=32, le=4096)


class TrainingRunRead(BaseModel):
    """One attempt at training a model, and what it was made from."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    dataset_version_id: str
    base_model_id: str
    job_id: str | None
    epochs: int
    image_size: int
    #: What was passed, plus anything the app had to decide for
    #: itself - notably which split validation read, when the export
    #: had no validation images.
    settings_json: dict | None
    status: str
    #: The model id the finished weights were imported under, once
    #: there is one.
    output_model_id: str | None
    best_map50: float | None
    last_epoch: int | None
    error_message: str | None
    started_at: datetime
    completed_at: datetime | None


class TrainingStarted(BaseModel):
    run: TrainingRunRead
    job_id: str
