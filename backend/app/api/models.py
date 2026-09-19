"""What can detect.

Until now the detector was one hard-coded file, so there was no way to
try a bigger model on a hard clip and no way at all to use one you
trained. This says what the choice is.
"""

from dataclasses import asdict

from pathlib import Path

from fastapi import APIRouter

from app.core.config import get_settings
from app.ml import models as model_registry
from app.ml.factory import get_detector
from app.schemas.model import ModelImportRequest, ModelRead
from app.services.model_import import import_model, remove_model

router = APIRouter(prefix="/models", tags=["models"])


@router.get("", response_model=list[ModelRead])
def list_models() -> list[ModelRead]:
    """Every model that can be chosen, built-in or your own.

    A built-in with no weights on disk yet is still listed: "not
    downloaded" is a state the first run resolves, not a reason to
    hide the option.
    """
    weights_dir = get_settings().resolved_model_weights_dir()
    return [ModelRead(**asdict(model)) for model in model_registry.list_models(weights_dir)]


@router.post("", response_model=ModelRead, status_code=201)
def import_a_model(payload: ModelImportRequest) -> ModelRead:
    """Take a `.pt` you trained into the models directory.

    The file is loaded as part of accepting it, which takes a few
    seconds and is the point: a checkpoint that does not load is
    refused here, rather than failing in the middle of a run where it
    reaches the user as a failed job.
    """
    weights_dir = get_settings().resolved_model_weights_dir()
    imported = import_model(Path(payload.path), weights_dir, name=payload.name)
    return ModelRead(**asdict(imported.model))


@router.delete("/{model_id}", status_code=204)
def remove_a_model(model_id: str) -> None:
    """Forget a model you imported. Built-ins are refused."""
    weights_dir = get_settings().resolved_model_weights_dir()
    remove_model(model_id, weights_dir)
    # Otherwise the deleted model stays loaded and keeps detecting
    # for the life of the process.
    get_detector.cache_clear()
