import json
import random
import shutil
from dataclasses import dataclass
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
from app.services.annotations import chunked
from app.services.dataset_query import ExportFrame, query_export_frames
from app.services.dataset_split import SPLIT_TEST, SPLIT_TRAIN, SPLIT_VAL, compute_split
from app.services.dataset_validator import ValidationResult, validate_export
from app.services.frame_materializer import can_materialize, materialize_frames
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
) -> tuple[DatasetVersion, ValidationResult, dict]:
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

    # What is really going to be written, decided before anything else
    # happens. The split ratios, the decode pass and the version number
    # all have to be about those frames and no others: computing a
    # 80/10/10 split over fifty frames and then writing forty-five of
    # them is not the split that was asked for, and decoding the other
    # five is work for an image that never lands.
    planned = _plan(export_frames, class_id_to_index)
    if not planned:
        raise DatasetExportError(
            f"Nothing to export: {len(export_frames)} labelled frame(s) have boxes on them, "
            "but none of those boxes has a class yet."
        )

    frames_skipped_unclassified = len(export_frames) - len(planned)

    # Frames whose pixels are simply gone. A live stream cannot be
    # decoded a second time, so a frame captured from one before its
    # image was written has nothing left behind it - and its source's
    # path is an rtsp:// URL that no decoder will open as a file.
    #
    # Skipped and counted rather than fatal. Two unrecoverable frames
    # used to take the whole export down with them, which left the
    # other forty-five perfectly good labelled frames unexportable and
    # the person with an error message about a camera address.
    recoverable = [p for p in planned if can_materialize(db, p.frame)]
    frames_skipped_unrecoverable = len(planned) - len(recoverable)
    if not recoverable:
        raise DatasetExportError(
            f"Nothing to export: all {frames_skipped_unrecoverable} labelled frame(s) have lost their "
            "images. Frames captured from a live stream can only be exported if the stream was "
            "recorded, and frames from an imported video need that file still to be present."
        )
    planned = recoverable
    track_by_annotation = _tracks_by_annotation(db, export_frames)

    frames_by_id = {p.frame.id: p.frame for p in planned}
    sources_by_id: dict[str, Source] = {p.frame.source_id: p.source for p in planned}

    split_by_frame = compute_split(list(frames_by_id), train_ratio, val_ratio, test_ratio, split_seed)

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
    frames_with_unclassified_boxes = 0

    for entry in planned:
        frame = entry.frame
        frame_id = frame.id
        split = split_by_frame[frame_id]
        if entry.is_background:
            background_frames += 1

        stem = f"{frame.source_id}_{frame.frame_index:06d}"
        image_rel = f"images/{split}/{stem}.jpg"
        label_rel = f"labels/{split}/{stem}.txt"
        shutil.copy2(image_path_by_frame[frame_id], export_dir / image_rel)
        # A background frame's label file is empty, not a blank line -
        # YOLO reads the file, and a stray newline is a malformed row.
        body = "\n".join(
            format_yolo_label_line(class_index, normalized) for _, class_index, normalized in entry.objects
        )
        (export_dir / label_rel).write_text(body + "\n" if body else "", encoding="utf-8")

        manifest_objects = []
        for annotation, class_index, normalized in entry.objects:
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
                    # Plate text, colour, direction and the rest. A YOLO
                    # label line has room for a class and four numbers
                    # and nothing else, so the manifest is the only
                    # place this work can survive the export.
                    "attributes": dict(annotation.attributes or {}),
                }
            )

        # A frame where the detector found vehicles that never got an
        # accepted annotation is exported with those vehicles unlabeled,
        # which teaches the model they are background. Surfaced here (and
        # in the validator) rather than silently shipped.
        #
        # Suppressed only for a frame that is *finished*: every box on
        # it carries a class, so the human demonstrably went through the
        # objects and a detection without a box is one they declined.
        # Warning about that would be telling them off for doing the job.
        #
        # Deliberately not suppressed merely because the frame reads as
        # "labeled". A frame with one classified box and one the user
        # never got round to classifying is exactly the unfinished work
        # this warning exists for, and the unclassified box is dropped
        # from the label file - so staying quiet would hide the loss.
        # Nor for a background frame: "there is nothing in this picture"
        # is a strong claim to make over the detector's head, and worth
        # saying out loud.
        unlabeled = 0
        if not entry.is_finished:
            detected = db.scalar(select(func.count(FrameCandidate.id)).where(FrameCandidate.frame_id == frame.id)) or 0
            unlabeled = max(0, detected - len(manifest_objects))
        if unlabeled:
            partially_labeled_frames += 1
        if entry.unclassified_count:
            frames_with_unclassified_boxes += 1

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
                #: Boxes on this frame that have no class, and so were
                #: left out of the label file. The frame still exports -
                #: the classified boxes on it are real work - but the
                #: unclassified ones ship as background.
                "unclassified_box_count": entry.unclassified_count,
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
        #: Exported frames carrying at least one box with no class. Those
        #: boxes are not in the label file.
        "frames_with_unclassified_boxes": frames_with_unclassified_boxes,
        #: Labelled frames left out entirely because not one of their
        #: boxes had a class. Recorded so an export that quietly shrank
        #: can say by how much and why.
        "frames_skipped_unclassified": frames_skipped_unclassified,
        #: Labelled frames left out because their image is gone and
        #: cannot be decoded again. Recorded because the work was
        #: done and did not land, and the person should not have to
        #: infer that from a count that shrank.
        "frames_skipped_unrecoverable": frames_skipped_unrecoverable,
        "items": manifest_items,
    }
    (export_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    db.commit()
    db.refresh(dataset_version)

    validation = validate_export(export_dir, manifest, num_classes=len(class_schema))
    return dataset_version, validation, manifest_summary(manifest)


def manifest_summary(manifest: dict) -> dict:
    """The headline numbers of an export, defaulted and in a fixed order.

    Taken from the manifest because the manifest is the record of what
    was written. Counting dataset-item rows instead gave the number of
    *boxes* while calling it frames, and missed background frames
    entirely because they write an image and no items.
    """
    splits = (SPLIT_TRAIN, SPLIT_VAL, SPLIT_TEST, "total")
    return {
        "counts": {split: int(manifest.get("counts", {}).get(split, 0)) for split in splits},
        "object_counts": {split: int(manifest.get("object_counts", {}).get(split, 0)) for split in splits},
        "background_frames": int(manifest.get("background_frames", 0)),
        "frames_with_unclassified_boxes": int(manifest.get("frames_with_unclassified_boxes", 0)),
        "frames_skipped_unclassified": int(manifest.get("frames_skipped_unclassified", 0)),
        "frames_skipped_unrecoverable": int(manifest.get("frames_skipped_unrecoverable", 0)),
    }


@dataclass
class _PlannedFrame:
    """One frame the export has decided it is going to write.

    Built before the split is computed, the images are decoded or the
    version row is created, so every one of those is about frames that
    will really land on disk.
    """

    frame: Frame
    source: Source
    #: ``(annotation, class index, normalised bbox)`` for each box that
    #: will appear in the label file.
    objects: list[tuple[Annotation, int, tuple[float, float, float, float]]]
    #: Boxes on this frame with no class. They are dropped from the label
    #: file, which is a loss worth counting rather than swallowing.
    unclassified_count: int
    #: A human labelled this frame and it holds nothing at all.
    is_background: bool

    @property
    def is_finished(self) -> bool:
        """A human worked this whole frame and left nothing half-done.

        The test the partial-label warning is gated on, and it takes all
        three parts. ``labeled`` is set only by a canvas save, so it is
        what separates "someone went through this picture" from "someone
        reviewed one track that happens to appear in it" - the second
        says nothing about the rest of the frame, which is the case the
        warning has always been for. Every box having a class rules out
        work abandoned half way, whose unclassified boxes are dropped
        from the label file. And a background frame is never finished by
        this definition: claiming a picture is empty over the detector's
        head is exactly the claim worth saying out loud.
        """
        return self.frame.status == "labeled" and bool(self.objects) and self.unclassified_count == 0


def _plan(export_frames: list[ExportFrame], class_id_to_index: dict[int, int]) -> list[_PlannedFrame]:
    """Work out which frames will be written, and with what on them.

    A frame with no exportable box is dataset material only when a human
    put it there: saving zero boxes is the label "nothing here", and an
    image with an empty label file is the negative example that teaches
    it. ``query_export_frames`` has already established that positively -
    a background frame carries no annotation rows of any kind - so a
    frame that arrives here with boxes but no *classified* ones is
    something else entirely: unfinished work. Shipping it as background
    would train the model to ignore the very vehicles someone was
    part-way through labelling, so it is left out.
    """
    planned: list[_PlannedFrame] = []
    for entry in export_frames:
        frame = entry.frame
        objects = []
        unclassified = 0
        for annotation in entry.annotations:
            class_id = annotation.class_id
            if class_id is None or class_id not in class_id_to_index:
                # Not classified, or classified against a class this
                # project no longer has. Not exportable, not an error.
                unclassified += 1
                continue
            class_index = class_id_to_index[class_id]
            normalized = normalize_yolo_bbox(tuple(annotation.bbox_json), frame.width, frame.height)
            objects.append((annotation, class_index, normalized))

        is_background = not entry.annotations
        if not objects and not is_background:
            continue

        planned.append(
            _PlannedFrame(
                frame=frame,
                source=entry.source,
                objects=objects,
                unclassified_count=unclassified,
                is_background=is_background,
            )
        )
    return planned


def _tracks_by_annotation(db: Session, export_frames: list[ExportFrame]) -> dict[str, str]:
    """Which track each annotation came from, where there is one.

    Provenance only - the manifest records it so an exported box can be
    traced back to the review that produced it. Boxes drawn on the
    canvas are simply absent from the result: they belong to a frame and
    to no track, and the manifest records null for them.

    Looked up in one batched pass rather than by walking a relationship
    per annotation, which on a fifty-frame export is fifty round trips
    for a field nothing trains on. ``chunked`` keeps each ``IN`` clause
    under SQLite's bound-parameter cap.
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
    for chunk in chunked(candidate_ids):
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
