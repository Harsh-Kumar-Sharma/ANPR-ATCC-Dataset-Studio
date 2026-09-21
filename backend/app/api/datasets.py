import json
from pathlib import Path

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.api.projects import get_project_or_404
from app.services.class_definitions import class_schema_for
from app.core.errors import NotFoundError
from app.db.models.dataset_item import DatasetItem
from app.db.models.dataset_version import DatasetVersion
from app.db.session import get_db
from app.ml.yolo_detector import DEFAULT_MODEL_WEIGHTS
from app.schemas.active_learning import RetrainingHandoffResult
from app.schemas.dataset import DatasetExportRequest, DatasetExportResult, DatasetVersionRead, ValidationResultRead
from app.schemas.storage import ReclaimedRead
from app.services.cascade import directory_size, remove_tree
from app.services.dataset_archive import archive_bytes, archive_name, stream_archive
from app.services.dataset_export import export_dataset_version
from app.services.dataset_validator import validate_export
from app.services.retraining_handoff import write_retraining_handoff

project_datasets_router = APIRouter(prefix="/projects/{project_id}/dataset-versions", tags=["datasets"])
datasets_router = APIRouter(prefix="/dataset-versions", tags=["datasets"])


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
    dataset_version, validation, summary = export_dataset_version(
        db,
        project,
        Path(project.workspace_path),
        train_ratio=payload.train_ratio,
        val_ratio=payload.val_ratio,
        test_ratio=payload.test_ratio,
        split_seed=payload.split_seed,
    )
    # Straight from the manifest the export just wrote, handed back by
    # the exporter rather than re-read from disk - the file carries an
    # entry per frame and per box, which is a lot of JSON to parse for
    # five numbers the export already had in memory.
    return DatasetExportResult(
        dataset_version=DatasetVersionRead.model_validate(dataset_version),
        validation=ValidationResultRead(valid=validation.valid, errors=validation.errors, warnings=validation.warnings),
        **summary,
    )


@project_datasets_router.get("", response_model=list[DatasetVersionRead])
def list_dataset_versions(project_id: str, db: Session = Depends(get_db)) -> list[DatasetVersionRead]:
    project = get_project_or_404(db, project_id)
    versions = db.scalars(
        select(DatasetVersion).where(DatasetVersion.project_id == project_id).order_by(DatasetVersion.version.desc())
    )
    return [_read(project.workspace_path, version) for version in versions]


def _read(workspace_path: str, version: DatasetVersion) -> DatasetVersionRead:
    """One version, with what it weighs on disk."""
    export_dir = _manifest_path(workspace_path, version).parent
    return DatasetVersionRead.model_validate(version).model_copy(
        update={"bytes_on_disk": archive_bytes(export_dir)}
    )


@datasets_router.get("/{dataset_version_id}", response_model=DatasetVersionRead)
def get_dataset_version(dataset_version_id: str, db: Session = Depends(get_db)) -> DatasetVersion:
    return _get_dataset_version_or_404(db, dataset_version_id)


@datasets_router.delete("/{dataset_version_id}", response_model=ReclaimedRead)
def delete_dataset_version(dataset_version_id: str, db: Session = Depends(get_db)) -> ReclaimedRead:
    """Delete an exported dataset version and its files.

    The version is a snapshot, not the work it was made from: the
    labels stay, and the same frames export again next time. What goes
    is the copy on disk - usually the largest single thing a project
    holds - and the rows indexing it.

    It also releases the frames it was holding. A frame in an exported
    version cannot be deleted, because the manifest names it; once the
    version is gone, it can be.
    """
    version = _get_dataset_version_or_404(db, dataset_version_id)
    project = get_project_or_404(db, version.project_id)
    export_dir = _manifest_path(project.workspace_path, version).parent
    reclaimed = directory_size(export_dir)

    db.execute(delete(DatasetItem).where(DatasetItem.dataset_version_id == version.id))
    db.delete(version)
    db.commit()
    # After the commit, like every other delete here: files left behind
    # can be removed by hand, rows pointing at files that are gone
    # cannot be reasoned about.
    remove_tree(export_dir)

    return ReclaimedRead(
        reclaimed_bytes=reclaimed,
        detail=f"Deleted dataset version v{version.version}. The labels it was made from are untouched.",
    )


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


@datasets_router.get("/{dataset_version_id}/archive")
def download_dataset_archive(dataset_version_id: str, db: Session = Depends(get_db)) -> StreamingResponse:
    """This dataset version as one zip, for training somewhere else.

    Streamed rather than built on disk: a second copy of the dataset
    is the one thing a drive with single-digit gigabytes free cannot
    take, and the archive is sent as it is read.

    The ``data.yaml`` inside is not the one on disk. That one holds
    this machine's absolute path, which is precisely what breaks the
    moment the dataset is anywhere else.
    """
    version = _get_dataset_version_or_404(db, dataset_version_id)
    project = get_project_or_404(db, version.project_id)
    manifest_path = _manifest_path(project.workspace_path, version)
    export_dir = manifest_path.parent
    if not export_dir.is_dir():
        raise NotFoundError(f"Export directory not found on disk for dataset version: {dataset_version_id}")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {}
    class_names = _snapshot_class_names(manifest, export_dir, db, project.id)
    filename = archive_name(project.name, version.version)
    return StreamingResponse(
        stream_archive(export_dir, class_names, base_model=DEFAULT_MODEL_WEIGHTS),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
