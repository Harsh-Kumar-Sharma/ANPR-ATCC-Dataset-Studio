"""What can detect.

Until now the detector was one hard-coded file, so there was no way to
try a bigger model on a hard clip and no way at all to use one you
trained. This says what the choice is.
"""

from dataclasses import asdict

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

from app.core.config import get_settings
from app.core.errors import NotFoundError
from app.ml import models as model_registry
from app.ml.factory import get_detector
from app.schemas.model import ModelImportRequest, ModelRead
from app.services.model_import import import_model, remove_model

router = APIRouter(prefix="/models", tags=["models"])


@router.get("", response_model=list[ModelRead])
def list_models(project_id: str | None = None) -> list[ModelRead]:
    """Every model that can be chosen, built-in or your own.

    A built-in with no weights on disk yet is still listed: "not
    downloaded" is a state the first run resolves, not a reason to
    hide the option.

    ``project_id`` narrows the custom ones to that project's own. A
    model trained on one project's labels detects that project's
    classes, so it is noise in another project's menu.
    """
    weights_dir = get_settings().resolved_model_weights_dir()
    return [
        ModelRead(**asdict(model))
        for model in model_registry.list_models(weights_dir, project_id=project_id)
    ]


@router.post("", response_model=ModelRead, status_code=201)
def import_a_model(payload: ModelImportRequest) -> ModelRead:
    """Take a `.pt` you trained into the models directory.

    The file is loaded as part of accepting it, which takes a few
    seconds and is the point: a checkpoint that does not load is
    refused here, rather than failing in the middle of a run where it
    reaches the user as a failed job.
    """
    weights_dir = get_settings().resolved_model_weights_dir()
    imported = import_model(Path(payload.path), weights_dir, name=payload.name, project_id=payload.project_id)
    return ModelRead(**asdict(imported.model))


@router.delete("/{model_id}", status_code=204)
def remove_a_model(model_id: str) -> None:
    """Forget a model you imported. Built-ins are refused."""
    weights_dir = get_settings().resolved_model_weights_dir()
    remove_model(model_id, weights_dir)
    # Otherwise the deleted model stays loaded and keeps detecting
    # for the life of the process.
    get_detector.cache_clear()


@router.get("/{model_id}/weights")
def download_model_weights(model_id: str) -> FileResponse:
    """The model's .pt file, to take somewhere else - production, most
    likely. Streamed from disk; named after the model, so a download
    of several does not leave a folder of best.pt files.

    A built-in that has never been fetched has no file yet, and says so.
    """
    weights_dir = get_settings().resolved_model_weights_dir()
    model = model_registry.get_model(model_id, weights_dir)
    path = weights_dir / model.weights_file
    if not path.is_file():
        raise NotFoundError(
            f"{model.label} has not been downloaded to this server yet. Use it for one run first, "
            "which fetches it, or download it from Ultralytics directly."
        )
    return FileResponse(path, media_type="application/octet-stream", filename=f"{model.id}.pt")
