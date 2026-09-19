"""Which models can detect, and where their weights live.

The detector used to be one hard-coded file behind a cache, so there
was no way to try a bigger model on a hard clip and no way at all to
use a model you trained yourself. This is the list of what can be
chosen, and the rules for turning a chosen id into a file on disk.
"""

import re
from dataclasses import dataclass
from pathlib import Path

from app.core.errors import AppError, NotFoundError

#: An id is used to build a path, so it is checked before it ever gets
#: near one. No separators, no dots leading anywhere: a request for
#: "../../secrets" is a mistake at best.
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

#: The weights file extension we recognise as a model.
WEIGHTS_SUFFIX = ".pt"


@dataclass(frozen=True)
class BuiltinModel:
    id: str
    label: str
    weights_file: str
    note: str


#: The two the user asked for, and no more. A longer menu of models
#: nobody has tried is a worse starting point than two that differ in
#: the way that matters: speed against accuracy.
BUILTIN_MODELS: tuple[BuiltinModel, ...] = (
    BuiltinModel(
        id="yolo26n",
        label="YOLO26 nano",
        weights_file="yolo26n.pt",
        note="Fastest. The default, and enough for clear daytime footage.",
    ),
    BuiltinModel(
        id="yolo26s",
        label="YOLO26 small",
        weights_file="yolo26s.pt",
        note="Slower, usually finds more. Worth trying on a clip the nano model struggles with.",
    ),
)

DEFAULT_MODEL_ID = BUILTIN_MODELS[0].id

_BUILTIN_BY_ID = {model.id: model for model in BUILTIN_MODELS}
_BUILTIN_FILES = {model.weights_file for model in BUILTIN_MODELS}


@dataclass
class ModelInfo:
    """One model the app can be asked to detect with."""

    id: str
    label: str
    #: "builtin" or "custom". A custom model is one the user put in the
    #: models directory - trained here, or brought from elsewhere.
    kind: str
    weights_file: str
    #: False for a built-in whose weights have not been fetched yet.
    #: The UI can still offer it; the first run downloads it.
    present: bool
    bytes: int
    note: str = ""


def _size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def list_models(weights_dir: Path) -> list[ModelInfo]:
    """Everything that can be chosen: the built-ins, then whatever the
    user has put in the models directory.

    Built-ins are listed whether or not their weights are on disk -
    "not downloaded yet" is a state the first run resolves, not a
    reason to hide the option.
    """
    models = [
        ModelInfo(
            id=builtin.id,
            label=builtin.label,
            kind="builtin",
            weights_file=builtin.weights_file,
            present=(weights_dir / builtin.weights_file).is_file(),
            bytes=_size(weights_dir / builtin.weights_file),
            note=builtin.note,
        )
        for builtin in BUILTIN_MODELS
    ]

    for path in sorted(weights_dir.glob(f"*{WEIGHTS_SUFFIX}")):
        if path.name in _BUILTIN_FILES or not path.is_file():
            continue
        models.append(
            ModelInfo(
                id=path.stem,
                label=path.stem,
                kind="custom",
                weights_file=path.name,
                present=True,
                bytes=_size(path),
                note="Your own model.",
            )
        )
    return models


def get_model(model_id: str, weights_dir: Path) -> ModelInfo:
    """The chosen model, or an error naming what was asked for.

    An unknown id is a 404 rather than a silent fall back to the
    default: detecting with a different model than the one asked for
    and saying nothing is how two runs become incomparable.
    """
    _require_safe_id(model_id)
    for model in list_models(weights_dir):
        if model.id == model_id:
            return model
    raise NotFoundError(f"Unknown model: {model_id}")


def weights_path(model_id: str, weights_dir: Path) -> Path:
    """Where this model's weights belong. May not exist yet."""
    return weights_dir / get_model(model_id, weights_dir).weights_file


class BadModelIdError(AppError):
    """An id that could not name a model, and might name a file.

    Separate from "unknown model" because the two want different
    answers: one is a typo, the other is an attempt to reach outside
    the models directory.
    """

    code = "bad_model_id"


def _require_safe_id(model_id: str) -> None:
    if not _SAFE_ID.match(model_id or "") or ".." in model_id:
        raise BadModelIdError(f"Not a usable model id: {model_id!r}")
