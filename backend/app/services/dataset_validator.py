from dataclasses import dataclass, field
from pathlib import Path

# A box covering essentially the whole image is the signature of the old
# crop-based export (one cropped vehicle per image, box filling it). Real
# full-frame training data should never look like this, so it is worth
# catching rather than discovering after a wasted training run.
_FULL_FRAME_BOX_AREA = 0.98


@dataclass
class ValidationResult:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return len(self.errors) == 0


def validate_export(export_dir: Path, manifest: dict, num_classes: int) -> ValidationResult:
    """Check that what's on disk actually matches the manifest and is
    structurally valid YOLO data - the "integrity validator" build
    item. Runs automatically after every export (docs/02_IMPLEMENTATION_PLAN.md
    Phase 6 DoD: "a reproducible, validated dataset version can be
    exported") and is re-runnable standalone against any prior export.

    Manifest items are frames (Phase 10), each carrying one image, one
    label file, and one or more objects.
    """
    result = ValidationResult()

    classes_path = export_dir / "classes.txt"
    if not classes_path.is_file():
        result.errors.append("classes.txt is missing")
    else:
        class_lines = classes_path.read_text(encoding="utf-8").splitlines()
        if len(class_lines) != num_classes:
            result.errors.append(f"classes.txt has {len(class_lines)} lines, expected {num_classes}")

    seen_export_paths: set[str] = set()
    counts = {"train": 0, "val": 0, "test": 0}
    object_counts = {"train": 0, "val": 0, "test": 0}

    for item in manifest.get("items", []):
        image_path = export_dir / item["image_path"]
        label_path = export_dir / item["label_path"]
        expected_objects = len(item.get("objects", []))

        if item["image_path"] in seen_export_paths or item["label_path"] in seen_export_paths:
            result.errors.append(f"duplicate export path for frame {item['frame_id']}")
        seen_export_paths.add(item["image_path"])
        seen_export_paths.add(item["label_path"])

        if not image_path.is_file():
            result.errors.append(f"missing image file: {item['image_path']}")
        if not label_path.is_file():
            result.errors.append(f"missing label file: {item['label_path']}")
            continue

        lines = [line for line in label_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        if len(lines) != expected_objects:
            result.errors.append(
                f"label file {item['label_path']} has {len(lines)} line(s), "
                f"but the manifest lists {expected_objects} object(s)"
            )
            continue

        for line in lines:
            parts = line.split()
            if len(parts) != 5:
                result.errors.append(f"label file {item['label_path']} malformed: {line!r}")
                continue

            class_index_str, *coords_str = parts
            if not class_index_str.isdigit() or not (0 <= int(class_index_str) < num_classes):
                result.errors.append(f"label file {item['label_path']} has invalid class index: {class_index_str}")

            try:
                coords = [float(c) for c in coords_str]
            except ValueError:
                result.errors.append(f"label file {item['label_path']} has non-numeric coordinates")
                continue

            if not all(0.0 <= c <= 1.0 for c in coords):
                result.errors.append(f"label file {item['label_path']} has out-of-range coordinates: {coords}")
                continue

            _, _, box_width, box_height = coords
            if box_width * box_height >= _FULL_FRAME_BOX_AREA:
                result.warnings.append(
                    f"{item['label_path']}: box covers {box_width * box_height:.1%} of the image - "
                    "this looks like a cropped image rather than a full frame"
                )

        split = item.get("split")
        if split in counts:
            counts[split] += 1
            object_counts[split] += expected_objects

    manifest_counts = manifest.get("counts", {})
    for split, actual in counts.items():
        expected = manifest_counts.get(split)
        if expected != actual:
            result.errors.append(f"manifest count for '{split}' is {expected}, but {actual} frame(s) were found")

    manifest_object_counts = manifest.get("object_counts", {})
    for split, actual in object_counts.items():
        expected = manifest_object_counts.get(split)
        if expected is not None and expected != actual:
            result.errors.append(f"manifest object count for '{split}' is {expected}, but {actual} were found")

    partially_labeled = manifest.get("partially_labeled_frames", 0)
    if partially_labeled:
        result.warnings.append(
            f"{partially_labeled} exported frame(s) contain detected vehicles with no accepted annotation. "
            "Those vehicles are exported as background, which teaches the model to ignore them."
        )
    # Note this only counts vehicles the *detector* found and a human did
    # not accept. A vehicle the detector missed entirely is invisible here
    # and still exported as background - only full-frame human labeling
    # (Phase 11) can catch that case.

    return result
