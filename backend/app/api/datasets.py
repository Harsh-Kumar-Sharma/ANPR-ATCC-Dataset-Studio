import json
from pathlib import Path

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.projects import get_project_or_404
from app.services.class_definitions import class_schema_for
from app.core.errors import NotFoundError
from app.db.models.dataset_version import DatasetVersion
from app.db.session import get_db
from app.ml.yolo_detector import DEFAULT_MODEL_WEIGHTS
from app.schemas.active_learning import RetrainingHandoffResult
from app.schemas.dataset import DatasetExportRequest, DatasetExportResult, DatasetVersionRead, ValidationResultRead
from app.services.dataset_export import export_dataset_version
from app.services.dataset_split import SPLIT_TEST, SPLIT_TRAIN, SPLIT_VAL
from app.services.dataset_validator import validate_export
from app.services.retraining_handoff import write_retraining_handoff

project_datasets_router = APIRouter(prefix="/projects/{project_id}/dataset-versions", tags=["datasets"])
datasets_router = APIRouter(prefix="/dataset-versions", tags=["datasets"])


def _split_counts(counts: dict) -> dict[str, int]:
    """The per-split totals, defaulted, in a fixed order.

    A manifest omits nothing, but an export with an empty split still has
    to read as zero rather than as a missing key on the way to the UI.
    """
    return {split: int(counts.get(split, 0)) for split in (SPLIT_TRAIN, SPLIT_VAL, SPLIT_TEST, "total")}


def _get_dataset_version_or_404(db: Session, dataset_version_id: str) -> DatasetVersion:
    version = db.get(DatasetVersion, dataset_version_id)
    if version is None:
        raise NotFoundError(f"Dataset version not found: {dataset_version_id}")
    return version


def _manifest_path(project_workspace_path: str, version: DatasetVersion) -> Path:
    return Path(project_workspace_path) / "exports" / f"v{version.version}" / "manifest.json"


@project_datasets_router.post("", response_model=DatasetExportResult, status_code=201)
def create_dataset_version(
    project_id: str, payload: DatasetExportRequest, db: Session = Depends(get_db)
) -> DatasetExportResult:
    """Export a new, immutable dataset version from every currently
    accepted annotation. docs/02_IMPLEMENTATION_PLAN.md Phase 6 DoD:
    "a reproducible, validated dataset version can be exported"."""
    project = get_project_or_404(db, project_id)
    dataset_version, validation = export_dataset_version(
        db,
        project,
        Path(project.workspace_path),
        train_ratio=payload.train_ratio,
        val_ratio=payload.val_ratio,
        test_ratio=payload.test_ratio,
        split_seed=payload.split_seed,
    )
    # Straight from the manifest the export just wrote, rather than
    # recounted here. These numbers used to come from a count of
    # dataset-item rows, which are one per *box* - so a frame with two
    # vehicles on it reported as two frames, and a frame labelled as
    # empty (which writes an image and no items) reported as none.
    manifest = json.loads(_manifest_path(project.workspace_path, dataset_version).read_text(encoding="utf-8"))
    return DatasetExportResult(
        dataset_version=DatasetVersionRead.model_validate(dataset_version),
        counts=_split_counts(manifest["counts"]),
        object_counts=_split_counts(manifest["object_counts"]),
        background_frames=int(manifest.get("background_frames", 0)),
        validation=ValidationResultRead(valid=validation.valid, errors=validation.errors, warnings=validation.warnings),
    )


@project_datasets_router.get("", response_model=list[DatasetVersionRead])
def list_dataset_versions(project_id: str, db: Session = Depends(get_db)) -> list[DatasetVersion]:
    get_project_or_404(db, project_id)
    return list(
        db.scalars(
            select(DatasetVersion).where(DatasetVersion.project_id == project_id).order_by(DatasetVersion.version.desc())
        )
    )


@datasets_router.get("/{dataset_version_id}", response_model=DatasetVersionRead)
def get_dataset_version(dataset_version_id: str, db: Session = Depends(get_db)) -> DatasetVersion:
    return _get_dataset_version_or_404(db, dataset_version_id)


@datasets_router.get("/{dataset_version_id}/manifest")
def get_dataset_manifest(dataset_version_id: str, db: Session = Depends(get_db)) -> dict:
    version = _get_dataset_version_or_404(db, dataset_version_id)
    project = get_project_or_404(db, version.project_id)
    manifest_path = _manifest_path(project.workspace_path, version)
    if not manifest_path.is_file():
        raise NotFoundError(f"Manifest not found on disk for dataset version: {dataset_version_id}")
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def _snapshot_class_names(manifest: dict, export_dir: Path, db: Session, project_id: str) -> list[str]:
    """The classes this version was exported against, in index order.

    Deliberately not "whatever the project has now": classes are
    editable, so a rename or reorder after export would make this
    disagree with the indices already written into the label files -
    validating a correct dataset as broken, and describing it to a
    trainer under the wrong names.

    Exports written before classes moved into the database have no
    snapshot in their manifest, but they do have the classes.txt sitting
    next to them, which is the same information. Falling back to the
    project's current list is the last resort, and only reached if both
    are missing.
    """
    snapshot = manifest.get("config", {}).get("classes")
    if snapshot is not None:
        return [c["name"] for c in snapshot]

    classes_txt = export_dir / "classes.txt"
    if classes_txt.is_file():
        return [line for line in classes_txt.read_text(encoding="utf-8").splitlines() if line.strip()]

    return [c["name"] for c in class_schema_for(db, project_id)]


@datasets_router.get("/{dataset_version_id}/validate", response_model=ValidationResultRead)
def revalidate_dataset_version(dataset_version_id: str, db: Session = Depends(get_db)) -> ValidationResultRead:
    """Re-run the integrity validator against whatever is currently on
    disk for this version - useful to confirm nothing has rotted
    (moved/deleted files) since export."""
    version = _get_dataset_version_or_404(db, dataset_version_id)
    project = get_project_or_404(db, version.project_id)
    manifest_path = _manifest_path(project.workspace_path, version)
    if not manifest_path.is_file():
        raise NotFoundError(f"Manifest not found on disk for dataset version: {dataset_version_id}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    num_classes = len(_snapshot_class_names(manifest, manifest_path.parent, db, version.project_id))
    result = validate_export(manifest_path.parent, manifest, num_classes=num_classes)
    return ValidationResultRead(valid=result.valid, errors=result.errors, warnings=result.warnings)


@datasets_router.post("/{dataset_version_id}/retraining-handoff", response_model=RetrainingHandoffResult)
def create_retraining_handoff(dataset_version_id: str, db: Session = Depends(get_db)) -> RetrainingHandoffResult:
    """Generate data.yaml + instructions for an external training job
    to pick up this dataset version (docs/02_IMPLEMENTATION_PLAN.md
    Phase 8: "retraining handoff"). Idempotent - safe to call again,
    it just rewrites the same two files."""
    version = _get_dataset_version_or_404(db, dataset_version_id)
    project = get_project_or_404(db, version.project_id)
    export_dir = _manifest_path(project.workspace_path, version).parent
    if not export_dir.is_dir():
        raise NotFoundError(f"Export directory not found on disk for dataset version: {dataset_version_id}")

    manifest_path = _manifest_path(project.workspace_path, version)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {}
    class_names = _snapshot_class_names(manifest, export_dir, db, project.id)
    paths = write_retraining_handoff(export_dir, class_names, base_model=DEFAULT_MODEL_WEIGHTS)
    return RetrainingHandoffResult(
        data_yaml_path=paths["data_yaml_path"],
        instructions_path=paths["instructions_path"],
        data_yaml_content=Path(paths["data_yaml_path"]).read_text(encoding="utf-8"),
        instructions_content=Path(paths["instructions_path"]).read_text(encoding="utf-8"),
    )
