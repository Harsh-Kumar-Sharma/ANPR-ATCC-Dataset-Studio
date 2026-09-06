from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ValidationResult:
    errors: list[str] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return len(self.errors) == 0


def validate_export(export_dir: Path, manifest: dict, num_classes: int) -> ValidationResult:
    """Check that what's on disk actually matches the manifest and is
    structurally valid YOLO data - the "integrity validator" build
    item. Runs automatically after every export (docs/02_IMPLEMENTATION_PLAN.md
    Phase 6 DoD: "a reproducible, validated dataset version can be
    exported") and is re-runnable standalone against any prior export.
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

    for item in manifest.get("items", []):
        image_path = export_dir / item["image_path"]
        label_path = export_dir / item["label_path"]

        if item["image_path"] in seen_export_paths or item["label_path"] in seen_export_paths:
            result.errors.append(f"duplicate export path for item {item['dataset_item_id']}")
        seen_export_paths.add(item["image_path"])
        seen_export_paths.add(item["label_path"])

        if not image_path.is_file():
            result.errors.append(f"missing image file: {item['image_path']}")
        if not label_path.is_file():
            result.errors.append(f"missing label file: {item['label_path']}")
            continue

        lines = [line for line in label_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        if len(lines) != 1:
            result.errors.append(f"label file {item['label_path']} should have exactly 1 line, has {len(lines)}")
            continue

        parts = lines[0].split()
        if len(parts) != 5:
            result.errors.append(f"label file {item['label_path']} malformed: {lines[0]!r}")
            continue

        class_index_str, *coords_str = parts
        if not class_index_str.isdigit() or not (0 <= int(class_index_str) < num_classes):
            result.errors.append(f"label file {item['label_path']} has invalid class index: {class_index_str}")

        try:
            coords = [float(c) for c in coords_str]
        except ValueError:
            result.errors.append(f"label file {item['label_path']} has non-numeric coordinates")
        else:
            if not all(0.0 <= c <= 1.0 for c in coords):
                result.errors.append(f"label file {item['label_path']} has out-of-range coordinates: {coords}")

        split = item.get("split")
        if split in counts:
            counts[split] += 1

    manifest_counts = manifest.get("counts", {})
    for split, actual in counts.items():
        expected = manifest_counts.get(split)
        if expected != actual:
            result.errors.append(f"manifest count for '{split}' is {expected}, but {actual} items were found")

    return result
