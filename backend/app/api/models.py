"""What can detect.

Until now the detector was one hard-coded file, so there was no way to
try a bigger model on a hard clip and no way at all to use one you
trained. This says what the choice is.
"""

from dataclasses import asdict

from fastapi import APIRouter

from app.core.config import get_settings
from app.ml import models as model_registry
from app.schemas.model import ModelRead

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
