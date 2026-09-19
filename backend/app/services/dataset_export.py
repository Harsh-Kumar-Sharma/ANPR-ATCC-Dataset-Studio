import json
import random
import shutil
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.services.class_definitions import class_schema_for
from app.core.errors import AppError
from app.db.models.annotation import Annotation
from app.db.models.dataset_item import DatasetItem
from app.db.models.dataset_version import DatasetVersion
from app.db.models.frame import Frame
from app.db.models.frame_candidate import FrameCandidate
from app.db.models.project import Project
from app.db.models.source import Source
from app.services.dataset_query import ExportFrame, query_export_frames
from app.services.dataset_split import SPLIT_TEST, SPLIT_TRAIN, SPLIT_VAL, compute_split
from app.services.dataset_validator import ValidationResult, validate_export
from app.services.frame_materializer import materialize_frames
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
    export_frames = query_export_frames(db, project.id)
    if not export_frames:
        raise DatasetExportError("No accepted annotations to export yet.")

    if split_seed is None:
        split_seed = random.randint(0, 2**31 - 1)

    class_schema = class_schema_for(db, project.id)
    class_id_to_index = {c["id"]: i for i, c in enumerate(class_schema)}

    items_by_frame: dict[str, list[Annotation]] = {}
    frames_by_id: dict[str, Frame] = {}
    sources_by_id: dict[str, Source] = {}
    for entry in export_frames:
        items_by_frame[entry.frame.id] = entry.annotations
        frames_by_id[entry.frame.id] = entry.frame
        sources_by_id[entry.frame.source_id] = entry.source

    track_by_annotation = _tracks_by_annotation(db, export_frames)

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
    background_frames = 0

    for frame_id, annotations in sorted(items_by_frame.items()):
        frame = frames_by_id[frame_id]
        split = split_by_frame[frame_id]

        label_lines = []
        objects = []
        for annotation in annotations:
            class_id = annotation.class_id
            if class_id is None or class_id not in class_id_to_index:
                continue  # not classified (or schema mismatch) - not exportable, but not an error either
            class_index = class_id_to_index[class_id]
            normalized = normalize_yolo_bbox(tuple(annotation.bbox_json), frame.width, frame.height)
            label_lines.append(format_yolo_label_line(class_index, normalized))
            objects.append((annotation, class_index, normalized))

        # A frame with no exportable box is only dataset material if a
        # human put it there: saving zero boxes is the label "nothing
        # here", and an image with an empty label file is the negative
        # example that teaches it. A frame whose only boxes are
        # unclassified is not that - it is unfinished work, and shipping
        # it as background would train the model to ignore the very
        # vehicles someone was part-way through labelling.
        is_background = not label_lines
        if is_background and (frame.status != "labeled" or annotations):
            continue
        if is_background:
            background_frames += 1

        stem = f"{frame.source_id}_{frame.frame_index:06d}"
        image_rel = f"images/{split}/{stem}.jpg"
        label_rel = f"labels/{split}/{stem}.txt"
        shutil.copy2(image_path_by_frame[frame_id], export_dir / image_rel)
        # A background frame's label file is empty, not a blank line -
        # YOLO reads the file, and a stray newline is a malformed row.
        body = "\n".join(label_lines)
        (export_dir / label_rel).write_text(body + "\n" if body else "", encoding="utf-8")

        manifest_objects = []
        for annotation, class_index, normalized in objects:
            dataset_item = DatasetItem(
                dataset_version_id=dataset_version.id,
                annotation_id=annotation.id,
                split=split,
                export_path=image_rel,
            )
            db.add(dataset_item)
            db.flush()
            manifest_objects.append(
                {
                    "dataset_item_id": dataset_item.id,
                    # Null for a box drawn on the canvas, which belongs to
                    # the frame and to no track. Recording that is honest;
                    # inventing a track would make the manifest lie about
                    # where a label came from.
                    "track_id": track_by_annotation.get(annotation.id),
                    "annotation_id": annotation.id,
                    "frame_candidate_id": annotation.frame_candidate_id,
                    "class_id": annotation.class_id,
                    "class_name": class_schema[class_index]["name"],
                    "bbox_yolo": list(normalized),
                }
            )

        # A frame where the detector found vehicles that never got an
        # accepted annotation is exported with those vehicles unlabeled,
        # which teaches the model they are background. Surfaced here (and
        # in the validator) rather than silently shipped.
        #
        # Only for frames nobody finished. A frame a human labelled on
        # the canvas has had every object on it looked at, so a detection
        # without a box is one they declined - warning about it would be
        # telling them off for doing the job. The heuristic is for frames
        # that only ever went through track review, where "reviewed one
        # track" really does leave the rest of the frame unlabelled.
        unlabeled = 0
        if frame.status != "labeled":
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
        #: Frames a human labelled as holding nothing, exported with an
        #: empty label file. Counted apart because a background image is
        #: a deliberate negative example, and a dataset that is mostly
        #: them is a problem you want to be able to see.
        "background_frames": background_frames,
        "items": manifest_items,
    }
    (export_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    db.commit()
    db.refresh(dataset_version)

    validation = validate_export(export_dir, manifest, num_classes=len(class_schema))
    return dataset_version, validation


#: SQLite refuses a statement with more than 32 766 bound parameters, so
#: an IN clause built from a Python list has to be fed in chunks.
_STATEMENT_BATCH = 500


def _tracks_by_annotation(db: Session, export_frames: list[ExportFrame]) -> dict[str, str]:
    """Which track each annotation came from, where there is one.

    Provenance only - the manifest records it so an exported box can be
    traced back to the review that produced it. Boxes drawn on the
    canvas are simply absent from the result: they belong to a frame and
    to no track, and the manifest records null for them.

    Looked up in one batched pass rather than by walking a relationship
    per annotation, which on a fifty-frame export is fifty round trips
    for a field nothing trains on.
    """
    candidate_ids = [
        annotation.frame_candidate_id
        for entry in export_frames
        for annotation in entry.annotations
        if annotation.frame_candidate_id is not None
    ]
    if not candidate_ids:
        return {}

    track_by_candidate: dict[str, str] = {}
    for start in range(0, len(candidate_ids), _STATEMENT_BATCH):
        chunk = candidate_ids[start : start + _STATEMENT_BATCH]
        rows = db.execute(
            select(FrameCandidate.id, FrameCandidate.track_id).where(FrameCandidate.id.in_(chunk))
        ).all()
        track_by_candidate.update({candidate_id: track_id for candidate_id, track_id in rows})

    return {
        annotation.id: track_by_candidate[annotation.frame_candidate_id]
        for entry in export_frames
        for annotation in entry.annotations
        if annotation.frame_candidate_id in track_by_candidate
    }
