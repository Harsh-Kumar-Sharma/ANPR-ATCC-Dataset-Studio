"""Training a model inside the app.

The app used to write a `data.yaml` and a `RETRAINING.md` and stop
there, leaving the user to copy a command into a terminal. This runs
that command for them, on the job machinery that already carries
long work: a detached process, a progress file, and a cancel.

One at a time, deliberately. The GPU here is small and training is
the one job that will saturate it; a second run started by accident
would make both slower and could exhaust GPU memory outright.
"""

import logging
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import AppError, ConflictError, NotFoundError
from app.db.models.dataset_version import DatasetVersion
from app.db.models.project import Project
from app.db.models.training_run import TrainingRun
from app.ml.models import WEIGHTS_SUFFIX, get_model, write_sidecar
from app.ml.weights import ensure_weights
from app.services.model_import import safe_id
from app.services.run_estimate import free_bytes_for

logger = logging.getLogger(__name__)

#: Augmentation for a number-plate detector, passed to Ultralytics.
#:
#: Its defaults are for general objects and mirror half the training
#: images left-right. A mirrored plate does not exist on a real road -
#: the model is taught characters backwards - so plates turn that off,
#: and keep rotation and shear small: a gantry camera sees plates
#: nearly level. Mosaic stays on but is closed for the last epochs so
#: training ends on whole, natural frames; patience stops a run that
#: has stopped improving instead of spending the rest of its epochs.
PLATE_AUGMENTATION: dict[str, float | int] = {
    "fliplr": 0.0,
    "flipud": 0.0,
    "degrees": 5.0,
    "shear": 2.0,
    "perspective": 0.0005,
    "translate": 0.1,
    "scale": 0.5,
    "mosaic": 1.0,
    "close_mosaic": 10,
    "mixup": 0.0,
    "patience": 30,
}


def augmentation_for(class_names: list[str]) -> dict[str, float | int]:
    """The plate settings when any class is a plate; Ultralytics' own
    defaults otherwise - a mirrored car is still a car."""
    if any("plate" in name.lower() for name in class_names):
        return dict(PLATE_AUGMENTATION)
    return {}


#: Statuses that mean the run is still going, and so still hold the GPU.
UNFINISHED = ("pending", "running")

#: Refuse to start a run that would leave less than this free.
#: Checkpoints, cached labels and the copied dataset all land on disk
#: while it runs, and a training run that fills the disk at epoch 80
#: wastes an hour rather than a minute.
SPACE_FLOOR_BYTES = 2 * 1024**3


class TrainingBusyError(ConflictError):
    """Another training run already has the GPU."""

    code = "training_busy"


class TrainingUnavailableError(AppError):
    code = "training_unavailable"


@dataclass
class TrainingProgress:
    """What one epoch reported."""

    epoch: int
    epochs: int
    loss: float | None = None
    map50: float | None = None

    def message(self) -> str:
        parts = [f"Epoch {self.epoch} of {self.epochs}"]
        if self.loss is not None:
            parts.append(f"loss {self.loss:.3f}")
        if self.map50 is not None:
            parts.append(f"mAP50 {self.map50:.3f}")
        return " · ".join(parts)

    def fraction(self) -> float:
        if self.epochs <= 0:
            return 0.0
        # Held below 1.0: the run is not done until the weights have
        # been imported, and a bar that sits at 100% while something
        # is still happening is a bar that lies.
        return min(0.95, self.epoch / self.epochs * 0.95)


def unfinished_run(db: Session) -> TrainingRun | None:
    """The run currently holding the GPU, if there is one."""
    return db.scalar(select(TrainingRun).where(TrainingRun.status.in_(UNFINISHED)))


def require_gpu_free(db: Session) -> None:
    busy = unfinished_run(db)
    if busy is None:
        return
    raise TrainingBusyError(
        f"A training run started {busy.started_at:%H:%M} is still going, on dataset version "
        f"{busy.dataset_version_id}. Only one runs at a time - this machine has one GPU, and a "
        "second run would make both slower. Wait for it or cancel it first.",
    )


def training_root(workspace_path: Path) -> Path:
    return workspace_path / "training"


def run_directory(workspace_path: Path, run_id: str) -> Path:
    return training_root(workspace_path) / run_id


def dataset_yaml(workspace_path: Path, version: DatasetVersion) -> Path:
    """Where the export for this version wrote its data.yaml."""
    return workspace_path / "exports" / f"v{version.version}" / "data.yaml"


def count_images(export_dir: Path, split: str) -> int:
    directory = export_dir / "images" / split
    if not directory.is_dir():
        return 0
    return sum(1 for path in directory.iterdir() if path.is_file())


def choose_validation_split(export_dir: Path) -> tuple[str, str | None]:
    """Which split validation should read, and why if it is not "val".

    A small dataset splits to an empty validation set - four labelled
    frames came out as three train, none val, one test - and training
    refuses to start without one. Rather than failing on arithmetic
    the user did not do, validation is pointed at a split that has
    images, and the substitution is recorded so nobody reads the
    resulting mAP as a real measure.
    """
    if count_images(export_dir, "val") > 0:
        return "val", None
    if count_images(export_dir, "test") > 0:
        return (
            "test",
            "The export has no validation images, so this run validated on the test split instead. "
            "Label more frames for a real validation set.",
        )
    return (
        "train",
        "The export has no validation or test images, so this run validated on the images it trained "
        "on. Its mAP is not a measure of anything - label more frames.",
    )


def write_data_yaml(
    db: Session, project: Project, version: DatasetVersion, export_dir: Path, val_split: str = "val"
) -> Path:
    """Write the data.yaml an export is missing.

    Reuses the same writer the handoff endpoint uses, so a dataset
    trained in the app and one trained from a terminal are described
    by identical files.
    """
    import json

    from app.services.class_definitions import class_names_for
    from app.services.retraining_handoff import write_retraining_handoff

    manifest_path = export_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {}
    snapshot = manifest.get("class_schema") or []
    names = [c["name"] for c in snapshot] if snapshot else class_names_for(db, project.id)

    paths = write_retraining_handoff(export_dir, names, base_model="yolo26n.pt", val_split=val_split)
    return Path(paths["data_yaml_path"])


def _exported_class_names(db: Session, project: Project, export_dir: Path) -> list[str]:
    """The classes the export was made with, falling back to the project's."""
    import json

    from app.services.class_definitions import class_names_for

    manifest_path = export_dir / "manifest.json"
    if manifest_path.is_file():
        snapshot = json.loads(manifest_path.read_text(encoding="utf-8")).get("class_schema") or []
        if snapshot:
            return [c["name"] for c in snapshot]
        classes = json.loads(manifest_path.read_text(encoding="utf-8")).get("config", {}).get("classes") or []
        if classes:
            return [c["name"] for c in classes]
    return list(class_names_for(db, project.id))


def prepare(
    db: Session,
    project: Project,
    dataset_version: DatasetVersion,
    base_model_id: str,
    weights_dir: Path,
    epochs: int = 100,
    image_size: int = 640,
    settings: dict | None = None,
) -> TrainingRun:
    """Check everything a run needs, and record it as about to start.

    Checked here rather than in the worker because every one of these
    is the caller's mistake, and finding out an hour later through a
    failed job is a poor way to learn that a dataset was never
    exported.
    """
    require_gpu_free(db)

    workspace = Path(project.workspace_path)
    yaml_path = dataset_yaml(workspace, dataset_version)
    if not yaml_path.parent.is_dir():
        raise TrainingUnavailableError(
            f"Dataset version v{dataset_version.version} has no exported files at {yaml_path.parent}. "
            "Export it before training on it."
        )
    export_dir = yaml_path.parent
    if count_images(export_dir, "train") == 0:
        raise TrainingUnavailableError(
            f"Dataset version v{dataset_version.version} has no training images. Label some frames and "
            "export again."
        )

    # Written every time rather than only when missing: the file has
    # to name a validation split that actually has images in it, and
    # which one that is depends on what was exported.
    val_split, note = choose_validation_split(export_dir)
    write_data_yaml(db, project, dataset_version, export_dir, val_split=val_split)
    augmentation = augmentation_for(_exported_class_names(db, project, export_dir))

    # An unknown base model is a 404 from here, the same as everywhere
    # else a model id is accepted.
    get_model(base_model_id, weights_dir)

    free = free_bytes_for(workspace)
    if free < SPACE_FLOOR_BYTES:
        raise TrainingUnavailableError(
            f"Only {free // 1024**2} MB free. Training writes checkpoints as it goes, and a run that "
            "fills the disk at epoch 80 wastes an hour rather than a minute. Free some space first - "
            "the Storage tool can tell you where it went."
        )

    run = TrainingRun(
        project_id=project.id,
        dataset_version_id=dataset_version.id,
        base_model_id=base_model_id,
        epochs=epochs,
        image_size=image_size,
        settings_json={
            **(settings or {}),
            "validated_on": val_split,
            # Recorded on the run, so what it was trained with can be
            # read back later, and passed to the trainer from here.
            "augmentation": augmentation,
            **({"note": note} if note else {}),
        },
        status="pending",
    )
    db.add(run)
    db.flush()
    return run


#: Runs the training. Injected so tests never load torch.
Trainer = Callable[..., Path]


def train_with_ultralytics(
    weights: Path,
    data_yaml: Path,
    output_dir: Path,
    epochs: int,
    image_size: int,
    device: str | None,
    on_epoch: Callable[[TrainingProgress], None],
    augmentation: dict | None = None,
) -> Path:
    """Run Ultralytics training and return the best checkpoint.

    Imported inside the function: ultralytics pulls in torch, which is
    seconds of import time that a process not training should not pay.
    """
    from ultralytics import YOLO

    model = YOLO(str(weights))

    def report(trainer) -> None:
        # Ultralytics hands the trainer over; everything useful is on
        # it, and all of it is optional depending on the version.
        metrics = getattr(trainer, "metrics", None) or {}
        losses = getattr(trainer, "label_loss_items", None)
        loss = None
        if callable(losses):
            try:
                loss = float(sum(losses(getattr(trainer, "tloss", None)).values()))
            except Exception:  # noqa: BLE001 - progress must not break training
                loss = None
        on_epoch(
            TrainingProgress(
                epoch=int(getattr(trainer, "epoch", 0)) + 1,
                epochs=epochs,
                loss=loss,
                map50=_first_float(metrics, ("metrics/mAP50(B)", "metrics/mAP50", "mAP50")),
            )
        )

    model.add_callback("on_fit_epoch_end", report)
    settings = get_settings()
    results = model.train(
        data=str(data_yaml.resolve()),
        epochs=epochs,
        imgsz=image_size,
        device=device,
        # Host memory, not GPU memory, is what runs out first: each
        # dataloader worker is a process of its own. See Settings.
        workers=settings.train_workers,
        batch=settings.train_batch,
        # Absolute, always. A relative project path is resolved by
        # ultralytics against its own runs directory, not against the
        # working directory - so the checkpoints landed under
        # backend/runs/detect/<the whole relative path again> and the
        # run was reported as having produced nothing.
        project=str(output_dir.resolve()),
        name="run",
        exist_ok=True,
        **(augmentation or {}),
    )

    best = _written_checkpoint(model, results, output_dir)
    if best is None:
        raise TrainingUnavailableError(
            f"Training finished but left no checkpoint under {output_dir.resolve()}"
        )
    return best


# --- testing what was trained ---------------------------------------------

#: Evaluates a checkpoint on one image list. Injected so tests never load torch.
Evaluator = Callable[..., dict]


def test_subsets(export_dir: Path) -> dict[str, list[Path]]:
    """The test images, all together and - when the export marks any of
    them as night shots - split into day and night.

    A model's overall score hides its worst condition: a night recall of
    0.80 disappears inside an average over mostly daytime frames. Night
    is read off the boxes' "Night shot" attribute; a background image
    (no boxes) counts only towards the whole.
    """
    import json

    manifest_path = export_dir / "manifest.json"
    if not manifest_path.is_file():
        return {}
    items = [
        item
        for item in json.loads(manifest_path.read_text(encoding="utf-8")).get("items", [])
        if item.get("split") == "test"
    ]
    if not items:
        return {}
    subsets: dict[str, list[Path]] = {"all": [export_dir / item["image_path"] for item in items]}
    night = [
        export_dir / item["image_path"]
        for item in items
        if any((obj.get("attributes") or {}).get("night") is True for obj in item.get("objects", []))
    ]
    if night:
        night_set = set(night)
        subsets["night"] = night
        subsets["day"] = [
            export_dir / item["image_path"]
            for item in items
            if item.get("objects") and export_dir / item["image_path"] not in night_set
        ]
    return {name: paths for name, paths in subsets.items() if paths}


def evaluate_on_test(
    evaluate: "Evaluator",
    weights: Path,
    export_dir: Path,
    data_yaml: Path,
    image_size: int,
    device: str | None,
) -> dict:
    """Precision, recall, mAP50 and mAP50-95 of a trained model on the
    export's test images, overall and by condition.

    Validation during training picks the best epoch, so its score is the
    best of many tries on those images. The test split was never looked
    at, which makes it the honest number. Each subset is written as an
    image list beside the export and handed to Ultralytics as its own
    split; labels are found from the image paths as usual.
    """
    import yaml

    subsets = test_subsets(export_dir)
    if not subsets:
        return {"note": "The export has no test images, so the model was not tested. Label more frames."}
    base = yaml.safe_load(data_yaml.read_text(encoding="utf-8"))
    results: dict = {}
    for name, images in subsets.items():
        listing = export_dir / f"test_{name}.txt"
        listing.write_text("\n".join(str(p.resolve()) for p in images) + "\n", encoding="utf-8")
        subset_yaml = export_dir / f"data_test_{name}.yaml"
        subset_yaml.write_text(
            yaml.safe_dump({**base, "path": str(export_dir.resolve()), "test": str(listing.resolve())}),
            encoding="utf-8",
        )
        metrics = evaluate(weights=weights, data_yaml=subset_yaml, image_size=image_size, device=device)
        results[name] = {**metrics, "images": len(images)}
    return results


def evaluate_with_ultralytics(weights: Path, data_yaml: Path, image_size: int, device: str | None) -> dict:
    """Run Ultralytics validation on a data.yaml's test split."""
    from ultralytics import YOLO

    settings = get_settings()
    metrics = YOLO(str(weights)).val(
        data=str(data_yaml.resolve()),
        split="test",
        imgsz=image_size,
        device=device,
        batch=settings.train_batch,
        workers=settings.train_workers,
        plots=False,
        verbose=False,
    )
    box = metrics.box
    return {
        "precision": round(float(box.mp), 4),
        "recall": round(float(box.mr), 4),
        "map50": round(float(box.map50), 4),
        "map50_95": round(float(box.map), 4),
    }


def _written_checkpoint(model, results, output_dir: Path) -> Path | None:
    """Where the weights actually went.

    Asked of ultralytics first and guessed second. It reports its own
    save directory, and trusting that rather than reconstructing the
    path is what keeps this working when it decides to put things
    somewhere else.
    """
    candidates: list[Path] = []

    trainer = getattr(model, "trainer", None)
    if trainer is not None and getattr(trainer, "best", None):
        candidates.append(Path(trainer.best))

    save_dir = getattr(results, "save_dir", None) or getattr(trainer, "save_dir", None)
    if save_dir:
        candidates.append(Path(save_dir) / "weights" / "best.pt")
        candidates.append(Path(save_dir) / "weights" / "last.pt")

    candidates.append(output_dir.resolve() / "run" / "weights" / "best.pt")

    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _first_float(metrics: dict, keys: tuple[str, ...]) -> float | None:
    for key in keys:
        value = metrics.get(key)
        if value is not None:
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
    return None


def adopt_weights(best: Path, weights_dir: Path, run: TrainingRun, version: int) -> str:
    """Put the trained weights where the model picker will find them.

    Named after the dataset version it was trained on rather than
    "best", because "best.pt" tells you nothing six weeks later and
    the models directory is a flat list.
    """
    base = safe_id(f"{run.base_model_id}-v{version}")
    model_id = base
    suffix = 2
    while (weights_dir / f"{model_id}{WEIGHTS_SUFFIX}").exists():
        # Training the same pair twice is a normal thing to do; the
        # second result must not overwrite the first.
        model_id = f"{base}-{suffix}"
        suffix += 1

    weights_dir.mkdir(parents=True, exist_ok=True)
    destination = weights_dir / f"{model_id}{WEIGHTS_SUFFIX}"
    shutil.copy2(best, destination)

    # Owned by the project whose labels made it. A model trained on
    # one project's classes is noise in every other project's menu.
    write_sidecar(
        weights_dir,
        model_id,
        label=model_id,
        project_id=run.project_id,
        trained_from=run.base_model_id,
        dataset_version=version,
    )
    return model_id


def base_weights(base_model_id: str, weights_dir: Path) -> Path:
    """The checkpoint a run starts from, fetched if it is a built-in
    that has never been used here."""
    return ensure_weights(base_model_id, weights_dir)


def settle(db: Session, run_id: str, status: str, message: str | None = None) -> None:
    """Leave a run that stopped without finishing in an honest state.

    Without this the row stays "running" forever, which both
    misreports what happened and holds the GPU against every future
    run.
    """
    run = db.get(TrainingRun, run_id)
    if run is None or run.status not in UNFINISHED:
        return
    run.status = status
    if message:
        run.error_message = message[:2048]
    run.completed_at = datetime.now(timezone.utc)
    db.commit()


def get_run(db: Session, run_id: str) -> TrainingRun:
    run = db.get(TrainingRun, run_id)
    if run is None:
        raise NotFoundError(f"Training run not found: {run_id}")
    return run


def forget(db: Session, run: TrainingRun) -> None:
    """Take a finished run out of the list.

    The row only. Whatever it produced stays: a model it trained is
    in the models directory and may be in use, and its checkpoints
    are on disk. Clearing a failure off a screen should not delete a
    model.

    A run still going is refused - forgetting it would free the GPU
    lock while the process carries on holding the GPU.
    """
    if run.status in UNFINISHED:
        raise TrainingBusyError(
            "That run is still going. Cancel it first - removing the row while the process "
            "continues would leave the app thinking the GPU is free."
        )
    db.delete(run)


def forget_finished(db: Session, project_id: str) -> int:
    """Clear this project's finished runs. Returns how many went."""
    runs = [
        run
        for run in db.scalars(select(TrainingRun).where(TrainingRun.project_id == project_id))
        if run.status not in UNFINISHED
    ]
    for run in runs:
        db.delete(run)
    return len(runs)
