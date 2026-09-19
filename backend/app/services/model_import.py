"""Bringing your own model into the app.

A model you trained is a `.pt` file somewhere on this machine. Until
now the only way to use one was to copy it into the models directory
by hand and know that the app would find it. This is that, done
properly: the file is checked before it is kept, and what is wrong
with it is said at import time rather than two minutes into a run.

Copied rather than referenced. A path into someone's Downloads folder
is a model that disappears, and a run that fails later with a missing
file is a worse answer than a copy that costs a few megabytes.
"""

import json
import logging
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from app.core.errors import AppError, NotFoundError
from app.ml.models import (
    BUILTIN_MODELS,
    WEIGHTS_SUFFIX,
    ModelInfo,
    get_model,
    list_models,
    sidecar_path,
)

logger = logging.getLogger(__name__)

#: Anything that is not a letter, digit, dash or underscore becomes a
#: dash, so the id is safe to put in a path and readable in a menu.
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")

#: A YOLO checkpoint is tens of megabytes. Much larger than this and
#: the file is probably not what the user thinks it is - but the limit
#: is generous, because a large custom model is a real thing.
MAX_WEIGHTS_BYTES = 2 * 1024**3


class ModelImportError(AppError):
    code = "model_import_failed"


@dataclass
class ImportedModel:
    model: ModelInfo
    #: What the model detects. The reason this matters is below, in
    #: ``_read_classes``.
    classes: list[str]


def safe_id(name: str) -> str:
    """A model id from whatever the user called their file."""
    cleaned = _UNSAFE.sub("-", name.strip()).strip("-._")
    return cleaned or "model"


def import_model(source_path: Path, weights_dir: Path, name: str | None = None) -> ImportedModel:
    """Take a `.pt` file into the models directory and check it works.

    Checked by loading it, which is slow - seconds - and worth every
    one of them: the alternative is a file that is accepted here and
    fails in the middle of a detection run, where the error reaches
    the user as a failed job rather than as "that is not a model".
    """
    source = Path(source_path).expanduser()
    _require_usable_file(source)

    model_id = safe_id(name or source.stem)
    _require_free_id(model_id, weights_dir)

    destination = weights_dir / f"{model_id}{WEIGHTS_SUFFIX}"
    weights_dir.mkdir(parents=True, exist_ok=True)

    # Copied to a temporary name first: a half-written file under the
    # real name would be listed as a model and loaded as one.
    staging = weights_dir / f".importing-{model_id}{WEIGHTS_SUFFIX}"
    try:
        shutil.copy2(source, staging)
        classes = _read_classes(staging)
        staging.replace(destination)
    except ModelImportError:
        staging.unlink(missing_ok=True)
        raise
    except OSError as exc:
        staging.unlink(missing_ok=True)
        raise ModelImportError(f"Could not copy {source} into the models directory: {exc}") from exc

    _write_sidecar(weights_dir, model_id, label=name or source.stem, classes=classes)
    logger.info("Imported model %s from %s (%d classes)", model_id, source, len(classes))
    return ImportedModel(model=get_model(model_id, weights_dir), classes=classes)


def remove_model(model_id: str, weights_dir: Path) -> None:
    """Forget a model you imported.

    Built-ins are refused: the id is not yours to free, and the next
    run would download the weights again anyway.
    """
    model = get_model(model_id, weights_dir)
    if model.kind != "custom":
        raise ModelImportError(f"{model_id} is a built-in model, not one you imported.")
    (weights_dir / model.weights_file).unlink(missing_ok=True)
    sidecar_path(weights_dir, model_id).unlink(missing_ok=True)


def _require_usable_file(source: Path) -> None:
    if not source.is_file():
        raise NotFoundError(f"No file at {source}")
    if source.suffix.lower() != WEIGHTS_SUFFIX:
        raise ModelImportError(
            f"A model has to be a {WEIGHTS_SUFFIX} file. {source.name} is not one."
        )
    size = source.stat().st_size
    if size == 0:
        raise ModelImportError(f"{source.name} is empty.")
    if size > MAX_WEIGHTS_BYTES:
        raise ModelImportError(
            f"{source.name} is {size / 1024**3:.1f} GB, which is far larger than a YOLO checkpoint. "
            "Check that this is the file you meant."
        )


def _require_free_id(model_id: str, weights_dir: Path) -> None:
    if any(builtin.id == model_id for builtin in BUILTIN_MODELS):
        raise ModelImportError(
            f"{model_id} is the name of a built-in model. Give yours a different name."
        )
    if any(model.id == model_id for model in list_models(weights_dir)):
        raise ModelImportError(
            f"There is already a model called {model_id}. Remove it first, or give this one another name."
        )


def _read_classes(weights: Path) -> list[str]:
    """Load the checkpoint and read what it detects.

    This is the validation: a file that does not load as a YOLO model
    is not one. The class names are kept because a custom model's
    classes are its own - a plate detector's "plate" is nothing like
    COCO's "car", and the detector must not filter its output against
    a list of COCO vehicle names it was never trained on.
    """
    try:
        from ultralytics import YOLO

        model = YOLO(str(weights))
        names = getattr(model, "names", None) or {}
    except Exception as exc:  # noqa: BLE001 - reported, not swallowed
        raise ModelImportError(
            f"That file did not load as a YOLO model: {exc}. "
            "It has to be an Ultralytics checkpoint - the .pt a training run writes."
        ) from exc

    classes = [str(name) for _, name in sorted(dict(names).items(), key=lambda item: item[0])]
    if not classes:
        raise ModelImportError("That model loaded but reports no classes, so it cannot detect anything.")
    return classes


def _write_sidecar(weights_dir: Path, model_id: str, label: str, classes: list[str]) -> None:
    """What we learned about the model, beside the model.

    Beside rather than inside the database: the models directory is
    the truth about which models exist - you can drop a file in it by
    hand - and a database row about a file that may be deleted behind
    our back is a row that lies.
    """
    try:
        sidecar_path(weights_dir, model_id).write_text(
            json.dumps({"label": label, "classes": classes}, indent=2), encoding="utf-8"
        )
    except OSError:
        # The model still works without it; the menu just shows the id
        # and the detector keeps every class, which is the safe way to
        # be wrong.
        logger.warning("Could not write the sidecar for %s", model_id, exc_info=True)
