import json
import random
import shutil
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.class_schema import get_class_schema
from app.core.errors import AppError
from app.db.models.dataset_item import DatasetItem
from app.db.models.dataset_version import DatasetVersion
from app.db.models.project import Project
from app.services.dataset_query import query_approved_items
from app.services.dataset_split import SPLIT_TEST, SPLIT_TRAIN, SPLIT_VAL, compute_split
from app.services.dataset_validator import ValidationResult, validate_export
from app.services.yolo_export import bbox_relative_to_crop, format_yolo_label_line, normalize_yolo_bbox


class DatasetExportError(AppError):
    code = "nothing_to_export"


def _next_version_number(db: Session, project_id: str) -> int:
    current_max = db.scalar(select(func.max(DatasetVersion.version)).where(DatasetVersion.project_id == project_id))
    return (current_max or 0) + 1


def export_dataset_version(
    db: Session,
    project: Project,
    workspace_path: Path,
    train_ratio: float = 0.8,
    val_ratio: float = 0.1,
    test_ratio: float = 0.1,
    split_seed: int | None = None,
) -> tuple[DatasetVersion, ValidationResult]:
    """Build a new, immutable, reproducible YOLO-format dataset
    version from every accepted annotation in the project.

    Phase 6 DoD (docs/02_IMPLEMENTATION_PLAN.md): "a reproducible,
    validated dataset version can be exported." Reproducible means a
    fixed (items, ratios, seed) always assigns the same split -
    ``split_seed`` is stored so this export can be explained/audited
    later, not so it can be literally re-run with byte-identical
    output (a later export would draw from whatever is accepted at
    that time, which is expected to grow).
    """
    approved = query_approved_items(db, project.id)
    if not approved:
        raise DatasetExportError("No accepted annotations to export yet.")

    if split_seed is None:
        split_seed = random.randint(0, 2**31 - 1)

    split_by_track = compute_split(
        [item.track.id for item in approved], train_ratio, val_ratio, test_ratio, split_seed
    )

    version_number = _next_version_number(db, project.id)
    export_dir = workspace_path / "exports" / f"v{version_number}"
    for split in (SPLIT_TRAIN, SPLIT_VAL, SPLIT_TEST):
        (export_dir / "images" / split).mkdir(parents=True, exist_ok=True)
        (export_dir / "labels" / split).mkdir(parents=True, exist_ok=True)

    class_schema = get_class_schema(project.class_schema_version)
    class_id_to_index = {c["id"]: i for i, c in enumerate(class_schema)}
    (export_dir / "classes.txt").write_text(
        "\n".join(c["name"] for c in class_schema) + "\n", encoding="utf-8"
    )

    config_snapshot = {
        "train_ratio": train_ratio,
        "val_ratio": val_ratio,
        "test_ratio": test_ratio,
        "class_schema_version": project.class_schema_version,
    }
    dataset_version = DatasetVersion(
        project_id=project.id, version=version_number, split_seed=split_seed, config_snapshot_json=config_snapshot
    )
    db.add(dataset_version)
    db.flush()

    manifest_items = []
    counts = {SPLIT_TRAIN: 0, SPLIT_VAL: 0, SPLIT_TEST: 0}

    for item in approved:
        if item.annotation.class_id is None or item.annotation.class_id not in class_id_to_index:
            continue  # not classified (or schema mismatch) - not exportable, but not an error either

        split = split_by_track[item.track.id]
        class_index = class_id_to_index[item.annotation.class_id]

        crop_width = item.frame_candidate.bbox_json[2] - item.frame_candidate.bbox_json[0]
        crop_height = item.frame_candidate.bbox_json[3] - item.frame_candidate.bbox_json[1]
        local_bbox = bbox_relative_to_crop(tuple(item.annotation.bbox_json), tuple(item.frame_candidate.bbox_json))
        normalized = normalize_yolo_bbox(local_bbox, crop_width, crop_height)
        label_line = format_yolo_label_line(class_index, normalized)

        image_rel = f"images/{split}/{item.track.id}.jpg"
        label_rel = f"labels/{split}/{item.track.id}.txt"
        shutil.copy2(item.frame_candidate.image_path, export_dir / image_rel)
        (export_dir / label_rel).write_text(label_line + "\n", encoding="utf-8")

        dataset_item = DatasetItem(
            dataset_version_id=dataset_version.id,
            annotation_id=item.annotation.id,
            split=split,
            export_path=image_rel,
        )
        db.add(dataset_item)
        db.flush()

        counts[split] += 1
        manifest_items.append(
            {
                "dataset_item_id": dataset_item.id,
                "track_id": item.track.id,
                "annotation_id": item.annotation.id,
                "frame_candidate_id": item.frame_candidate.id,
                "source_id": item.source.id,
                "source_path": item.source.path_or_uri,
                "timestamp_ms": item.frame_candidate.timestamp_ms,
                "class_id": item.annotation.class_id,
                "class_name": class_schema[class_index]["name"],
                "split": split,
                "image_path": image_rel,
                "label_path": label_rel,
            }
        )

    manifest = {
        "dataset_version_id": dataset_version.id,
        "project_id": project.id,
        "version": version_number,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "split_seed": split_seed,
        "config": config_snapshot,
        "counts": {**counts, "total": sum(counts.values())},
        "items": manifest_items,
    }
    (export_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    db.commit()
    db.refresh(dataset_version)

    validation = validate_export(export_dir, manifest, num_classes=len(class_schema))
    return dataset_version, validation
