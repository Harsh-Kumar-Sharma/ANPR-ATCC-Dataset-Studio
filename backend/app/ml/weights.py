"""Getting a model's weights onto disk before anything tries to load
them.

A built-in model is a name until its weights are fetched. Doing that
inside ``YOLO(...)`` would work, but the download lands wherever
ultralytics feels like putting it and the caller sees a minute of
silence with no idea why. Fetching it here means the file goes to the
models directory, and the wait is reported as progress.
"""

import logging
from collections.abc import Callable
from pathlib import Path

from app.core.errors import AppError
from app.ml.models import ModelInfo, get_model

logger = logging.getLogger(__name__)

#: Given the weights' filename and the directory they belong in,
#: put them there. Injected so tests never reach the network.
Fetcher = Callable[[str, Path], Path]


class WeightsUnavailableError(AppError):
    """The weights are not here and could not be fetched.

    Its own error because the answer is different from every other
    failure in a run: nothing is wrong with the video or the source,
    the app simply could not get the model.
    """

    code = "weights_unavailable"


def download_weights(weights_file: str, weights_dir: Path) -> Path:
    """Fetch a built-in's weights from ultralytics into our own
    directory.

    Imported inside the function because ultralytics is slow to import
    and most calls never get here - the weights are usually already on
    disk.
    """
    from ultralytics.utils.downloads import attempt_download_asset

    destination = weights_dir / weights_file
    weights_dir.mkdir(parents=True, exist_ok=True)
    fetched = Path(attempt_download_asset(str(destination)))
    if fetched != destination and fetched.is_file():
        # attempt_download_asset may satisfy the request from its own
        # cache and hand back a path elsewhere. Copy it in, so the next
        # run finds it where this app looks.
        destination.write_bytes(fetched.read_bytes())
    return destination


def ensure_weights(
    model_id: str,
    weights_dir: Path,
    *,
    report: Callable[[str], None] | None = None,
    fetch: Fetcher = download_weights,
) -> Path:
    """The path to this model's weights, downloading them once if the
    model is a built-in that has never been used here.

    A custom model is never fetched: there is nowhere to fetch it from,
    and inventing a download for a file the user was supposed to
    provide would turn a clear "it is not there" into a confusing
    network error.
    """
    return ensure_weights_for(get_model(model_id, weights_dir), weights_dir, report=report, fetch=fetch)


def ensure_weights_for(
    model: ModelInfo,
    weights_dir: Path,
    *,
    report: Callable[[str], None] | None = None,
    fetch: Fetcher = download_weights,
) -> Path:
    """The same, for a model that has already been looked up."""
    model_id = model.id
    path = weights_dir / model.weights_file
    if path.is_file():
        return path

    if model.kind != "builtin":
        raise WeightsUnavailableError(
            f"The weights for {model_id} are not in the models directory: {path}. "
            "A model you trained or imported has to be present as a file."
        )

    if report is not None:
        report(f"Fetching {model.label} weights - first use, this happens once")
    logger.info("Fetching weights for %s into %s", model_id, weights_dir)
    try:
        fetched = fetch(model.weights_file, weights_dir)
    except Exception as exc:  # noqa: BLE001 - reported, not swallowed
        raise WeightsUnavailableError(
            f"Could not download the weights for {model_id}: {exc}. "
            f"Put {model.weights_file} in {weights_dir} by hand, or choose a model that is already there."
        ) from exc

    if not Path(fetched).is_file():
        raise WeightsUnavailableError(
            f"The download for {model_id} reported success but left no file at {fetched}."
        )
    return Path(fetched)


def describe(model: ModelInfo) -> str:
    """How a model is named in a progress message or a log line."""
    return f"{model.label} ({model.id})"
