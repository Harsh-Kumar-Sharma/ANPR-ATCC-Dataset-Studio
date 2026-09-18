import json
import random
import shutil
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.services.class_definitions import class_schema_for
from app.core.errors import AppError
from app.db.models.dataset_item import DatasetItem
from app.db.models.dataset_version import DatasetVersion
from app.db.models.frame import Frame
from app.db.models.frame_candidate import FrameCandidate
from app.db.models.project import Project
from app.db.models.source import Source
from app.services.dataset_query import ApprovedItem, query_approved_items
from app.services.dataset_split import SPLIT_TEST, SPLIT_TRAIN, SPLIT_VAL, compute_split
from app.services.dataset_validator import ValidationResult, validate_export
from app.services.frame_materializer import get_or_create_frame, materialize_frames
from app.services.yolo_export import format_yolo_label_line, normalize_yolo_bbox


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
    """Build a new, immutable, reproducible YOLO-format dataset version
    from every accepted annotation in the project.

    Exports **full frames**, not per-detection crops (Phase 10,
    docs/13_LABELING_AND_TRAINING_PLAN.md). The earlier crop-based
    export produced one image per track with a box covering nearly the
    whole image, which trains a detector to expect a vehicle filling the
    frame - useless against real gantry footage where a vehicle occupies
    a few percent of the pixels.

    Two consequences follow from exporting whole frames:

    - **The split unit is the frame, not the track.** Two vehicles in
      one frame share one image, so assigning by track could place the
      same pixels in both train and val with a different subset of boxes
      each time - leakage plus wrong labels.
    - **Every accepted box on a frame goes in that frame's one label
      file**, so the model sees all the labeled objects in the image
      rather than one of them.

    Reproducible means a fixed (items, ratios, seed) always assigns the
    same split - ``split_seed`` is stored so this export can be
    explained/audited later.
    """
    approved = query_approved_items(db, project.id)
    if not approved:
        raise DatasetExportError("No accepted annotations to export yet.")

    if split_seed is None:
        split_seed = random.randint(0, 2**31 - 1)

    class_schema = class_schema_for(db, project.id)
    class_id_to_index = {c["id"]: i for i, c in enumerate(class_schema)}

    items_by_frame: dict[str, list[ApprovedItem]] = {}
    frames_by_id: dict[str, Frame] = {}
    sources_by_id: dict[str, Source] = {}
    for item in approved:
        frame = _resolve_frame(db, item)
        items_by_frame.setdefault(frame.id, []).append(item)
        frames_by_id[frame.id] = frame
        sources_by_id[frame.source_id] = item.source

    split_by_frame = compute_split(sorted(items_by_frame), train_ratio, val_ratio, test_ratio, split_seed)

    # One ordered decode pass per source video, rather than reopening the
    # video for every frame (see materialize_frames).
    image_path_by_frame: dict[str, Path] = {}
    for source_id, source in sources_by_id.items():
        source_frames = [f for f in frames_by_id.values() if f.source_id == source_id]
        image_path_by_frame.update(materialize_frames(db, source_frames, source, workspace_path))

    version_number = _next_version_number(db, project.id)
    export_dir = workspace_path / "exports" / f"v{version_number}"
    for split in (SPLIT_TRAIN, SPLIT_VAL, SPLIT_TEST):
        (export_dir / "images" / split).mkdir(parents=True, exist_ok=True)
        (export_dir / "labels" / split).mkdir(parents=True, exist_ok=True)

    (export_dir / "classes.txt").write_text("\n".join(c["name"] for c in class_schema) + "\n", encoding="utf-8")

    config_snapshot = {
        "train_ratio": train_ratio,
        "val_ratio": val_ratio,
        "test_ratio": test_ratio,
        "class_schema_version": project.class_schema_version,
        # The classes as they were at export time. Classes are editable
        # now, so the preset name alone can no longer answer "what was
        # this dataset trained against" - only a snapshot can.
        "classes": class_schema,
        "image_mode": "full_frame",
    }
    dataset_version = DatasetVersion(
        project_id=project.id, version=version_number, split_seed=split_seed, config_snapshot_json=config_snapshot
    )
    db.add(dataset_version)
    db.flush()

    manifest_items = []
    frame_counts = {SPLIT_TRAIN: 0, SPLIT_VAL: 0, SPLIT_TEST: 0}
    object_counts = {SPLIT_TRAIN: 0, SPLIT_VAL: 0, SPLIT_TEST: 0}
    partially_labeled_frames = 0

    for frame_id, items in sorted(items_by_frame.items()):
        frame = frames_by_id[frame_id]
        split = split_by_frame[frame_id]

        label_lines = []
        objects = []
        for item in items:
            class_id = item.annotation.class_id
            if class_id is None or class_id not in class_id_to_index:
                continue  # not classified (or schema mismatch) - not exportable, but not an error either
            class_index = class_id_to_index[class_id]
            normalized = normalize_yolo_bbox(tuple(item.annotation.bbox_json), frame.width, frame.height)
            label_lines.append(format_yolo_label_line(class_index, normalized))
            objects.append((item, class_index, normalized))

        if not label_lines:
            continue

        stem = f"{frame.source_id}_{frame.frame_index:06d}"
        image_rel = f"images/{split}/{stem}.jpg"
        label_rel = f"labels/{split}/{stem}.txt"
        shutil.copy2(image_path_by_frame[frame_id], export_dir / image_rel)
        (export_dir / label_rel).write_text("\n".join(label_lines) + "\n", encoding="utf-8")

        manifest_objects = []
        for item, class_index, normalized in objects:
            dataset_item = DatasetItem(
                dataset_version_id=dataset_version.id,
                annotation_id=item.annotation.id,
                split=split,
                export_path=image_rel,
            )
            db.add(dataset_item)
            db.flush()
            manifest_objects.append(
                {
                    "dataset_item_id": dataset_item.id,
                    "track_id": item.track.id,
                    "annotation_id": item.annotation.id,
                    "frame_candidate_id": item.frame_candidate.id,
                    "class_id": item.annotation.class_id,
                    "class_name": class_schema[class_index]["name"],
                    "bbox_yolo": list(normalized),
                }
            )

        # A frame where the detector found vehicles that never got an
        # accepted annotation is exported with those vehicles unlabeled,
        # which teaches the model they are background. Surfaced here (and
        # in the validator) rather than silently shipped - closing this
        # gap properly is Phase 11's full-frame labeling.
        detected = db.scalar(select(func.count(FrameCandidate.id)).where(FrameCandidate.frame_id == frame.id)) or 0
        unlabeled = max(0, detected - len(manifest_objects))
        if unlabeled:
            partially_labeled_frames += 1

        frame_counts[split] += 1
        object_counts[split] += len(manifest_objects)
        manifest_items.append(
            {
                "frame_id": frame.id,
                "source_id": frame.source_id,
                "source_path": sources_by_id[frame.source_id].path_or_uri,
                "frame_index": frame.frame_index,
                "timestamp_ms": frame.timestamp_ms,
                "width": frame.width,
                "height": frame.height,
                "split": split,
                "image_path": image_rel,
                "label_path": label_rel,
                "objects": manifest_objects,
                "unlabeled_detection_count": unlabeled,
            }
        )

    manifest = {
        "dataset_version_id": dataset_version.id,
        "project_id": project.id,
        "version": version_number,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "split_seed": split_seed,
        "config": config_snapshot,
        "counts": {**frame_counts, "total": sum(frame_counts.values())},
        "object_counts": {**object_counts, "total": sum(object_counts.values())},
        "partially_labeled_frames": partially_labeled_frames,
        "items": manifest_items,
    }
    (export_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    db.commit()
    db.refresh(dataset_version)

    validation = validate_export(export_dir, manifest, num_classes=len(class_schema))
    return dataset_version, validation


def _resolve_frame(db: Session, item: ApprovedItem) -> Frame:
    """Find the full frame an approved annotation belongs to, creating
    the row on the fly for data captured before frames existed.

    This is the backfill path: pre-Phase-10 ``frame_candidates`` have no
    ``frame_id``, but they do record ``frame_index``/``timestamp_ms``
    against a source video that is still in the workspace, so the frame
    is fully identifiable (and its pixels re-decodable) after the fact.
    """
    if item.frame_candidate.frame_id:
        frame = db.get(Frame, item.frame_candidate.frame_id)
        if frame is not None:
            return frame

    frame = get_or_create_frame(
        db,
        source_id=item.source.id,
        frame_index=item.frame_candidate.frame_index,
        timestamp_ms=item.frame_candidate.timestamp_ms,
        width=item.source.width,
        height=item.source.height,
    )
    item.frame_candidate.frame_id = frame.id
    return frame
